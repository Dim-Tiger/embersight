"""Values-at-Risk subagent.

Pass-2: spatial-join MS Building Footprints + USA Structures + CMS
hospitals + NCES schools + EIA transmission lines against the predicted
spread cone (24h bucket from spread_simulation — prefers the swept cone
over the WFIGS perimeter so VAR matches the map render exactly, with the
raw p50 cone as second choice, and a 2 km default radius around the
incident only when no spread output is present yet).

The LLM (Haiku 4.5) is given a fused tally and the prompt at
``prompts/values_at_risk.md`` and must produce a single narrative plus a
bullet list of headline findings. If ``ANTHROPIC_API_KEY`` is missing
we fall back to a deterministic templated narrative so the smoke test
remains hermetic.

No ``interrupt()`` is requested here — values-at-risk is informational.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

from ..state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Dataset,
    Incident,
    Model,
)
from ..tools.buildings import query_ms_buildings, query_usa_structures
from ..tools.evac import (
    DEFAULT_BUILDING_DENSITY_PER_KM2 as DENSITY_BUILDINGS_PER_KM2,
)
from ..tools.evac import DEFAULT_HOUSEHOLD_SIZE, estimate_population
from .provenance import narrator_model
from ..tools.infra import (
    query_critical_facilities,
    query_hospitals,
    query_schools,
    query_transmission_lines,
)

AGENT_NAME = "values_at_risk"
log = logging.getLogger(__name__)

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "values_at_risk.md"
)

DEFAULT_RADIUS_KM = 2.0
LLM_MODEL_ID = "claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _circle_wkt(lat: float, lon: float, radius_km: float, n: int = 64) -> str:
    """Build a closed equirectangular circle WKT polygon (good enough for
    bbox/ArcGIS envelope filtering — we never use this for true geodesic
    area math)."""
    # Approximate degrees-per-km at the given latitude.
    deg_per_km_lat = 1.0 / 110.574
    deg_per_km_lon = 1.0 / (111.320 * max(math.cos(math.radians(lat)), 1e-6))
    points: list[tuple[float, float]] = []
    for i in range(n):
        theta = (2 * math.pi * i) / n
        dlat = radius_km * math.cos(theta) * deg_per_km_lat
        dlon = radius_km * math.sin(theta) * deg_per_km_lon
        points.append((lon + dlon, lat + dlat))
    points.append(points[0])  # close the ring
    inner = ", ".join(f"{x} {y}" for x, y in points)
    return f"POLYGON(({inner}))"


def _bbox_from_wkt(polygon_wkt: str) -> tuple[float, float, float, float]:
    from shapely import wkt  # noqa: PLC0415

    geom = wkt.loads(polygon_wkt)
    minx, miny, maxx, maxy = geom.bounds
    return float(minx), float(miny), float(maxx), float(maxy)


def _cone_to_wkt(cone: Any, incident: Incident | None) -> str:
    """Convert a spread-simulation cone payload to a WKT polygon.

    Accepts either a WKT string, a GeoJSON dict, or None (fall back to the
    default radius around the incident).
    """
    if isinstance(cone, str) and cone.strip().upper().startswith("POLYGON"):
        return cone
    if isinstance(cone, dict):
        geom = cone.get("geometry", cone)
        if isinstance(geom, dict) and geom.get("type") in (
            "Polygon",
            "MultiPolygon",
        ):
            try:
                from shapely.geometry import shape  # noqa: PLC0415

                return shape(geom).wkt
            except Exception:  # noqa: BLE001
                pass
    if incident is None:
        # Degenerate but valid WKT — keeps downstream callers from crashing.
        return _circle_wkt(0.0, 0.0, DEFAULT_RADIUS_KM)
    return _circle_wkt(incident.lat, incident.lon, DEFAULT_RADIUS_KM)


def _spread_cone(state: AgentState) -> tuple[Any, str]:
    """Return (cone_payload, source_label).

    Preference order:
      1. ``swept_cone_24h`` — the 24h p50 cone Minkowski-swept over the WFIGS
         perimeter. This is the polygon painted on the map and the same one
         spread_simulation runs its own ``cone_impact`` against, so VAR stays
         consistent with the visual + the spread agent's numbers.
      2. ``cones.24h`` — raw 24h p50 ensemble cone (no perimeter sweep).
      3. None → caller falls back to a small radius around the incident.
    """
    spread = state.outputs.get("spread_simulation")
    if spread is not None:
        payload = spread.payload or {}
        swept = payload.get("swept_cone_24h")
        if swept:
            return swept, "spread_simulation.swept_cone_24h"
        cones = payload.get("cones") or {}
        cone_24h = cones.get("24h")
        if cone_24h:
            return cone_24h, "spread_simulation.24h"
    return None, f"default-radius-{DEFAULT_RADIUS_KM:.0f}km"


# --------------------------------------------------------------------------- #
# Tool fan-out
# --------------------------------------------------------------------------- #

# The six layers we query, in citation order. "Core" layers drive the
# structure and population numbers; losing one makes the headline unknown.
SOURCES: tuple[str, ...] = (
    "ms_buildings",
    "usa_structures",
    "hospitals",
    "schools",
    "transmission",
    "critical_facilities",
)
CORE_SOURCES = frozenset({"ms_buildings", "usa_structures"})


async def _gather_tallies(polygon_wkt: str) -> dict[str, Any]:
    """Run the six spatial queries in parallel against the polygon itself
    (each on a worker thread because the HTTP calls are synchronous)."""
    loop = asyncio.get_running_loop()
    coros = {
        "ms_buildings": loop.run_in_executor(None, query_ms_buildings, polygon_wkt),
        "usa_structures": loop.run_in_executor(
            None, query_usa_structures, polygon_wkt
        ),
        "hospitals": loop.run_in_executor(None, query_hospitals, polygon_wkt),
        "schools": loop.run_in_executor(None, query_schools, polygon_wkt),
        "transmission": loop.run_in_executor(
            None, query_transmission_lines, polygon_wkt
        ),
        "critical_facilities": loop.run_in_executor(
            None, query_critical_facilities, polygon_wkt
        ),
    }
    results: dict[str, Any] = {}
    for key, fut in coros.items():
        try:
            results[key] = await fut
        except Exception as exc:  # noqa: BLE001
            results[key] = {"error": f"task:{exc}"}
    return results


def _has_error(result: Any) -> bool:
    """True when a layer failed, fully or partly.

    Tools report failure three ways: ``{"error": ...}``, ``[{"error": ...}]``,
    or (critical facilities) ``{"errors": {sub_layer: msg}}``.
    """
    if isinstance(result, dict) and (result.get("error") or result.get("errors")):
        return True
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return bool(result[0].get("error"))
    return False


def errored_sources(tallies: dict[str, Any]) -> list[str]:
    return [k for k in SOURCES if _has_error(tallies.get(k))]


# --------------------------------------------------------------------------- #
# Tally rollup
# --------------------------------------------------------------------------- #


def _clean(rows: Any) -> list[dict[str, Any]]:
    return [r for r in (rows or []) if isinstance(r, dict) and "error" not in r]


def _population(
    residential: int | None, polygon_wkt: str | None
) -> tuple[int | None, str, str]:
    """Population exposed, with the method that produced it.

    Prefer counted residential structures x average household size. Only
    when the structure layer is unavailable fall back to a flat area-density
    guess, and say so: that number is a planning placeholder, not a count.
    """
    if residential is not None:
        return (
            int(round(residential * DEFAULT_HOUSEHOLD_SIZE)),
            "residential_structures",
            f"{residential:,} residential structures x "
            f"{DEFAULT_HOUSEHOLD_SIZE} people (USA Structures)",
        )
    if polygon_wkt:
        est = estimate_population(polygon_wkt)
        return (
            est,
            "area_density_estimate",
            "ROUGH ESTIMATE: structure data unavailable, so area x "
            f"{DENSITY_BUILDINGS_PER_KM2:.0f} buildings/km² x "
            f"{DEFAULT_HOUSEHOLD_SIZE} people",
        )
    return None, "unavailable", "population unavailable"


def _rollup(tallies: dict[str, Any], polygon_wkt: str | None = None) -> dict[str, Any]:
    """Fuse the six layers into one tally.

    A count from a layer that failed is ``None`` ("unknown"), never 0, so a
    feed outage cannot read as "nothing at risk". ``unavailable`` lists the
    failed layers.
    """
    errored = set(errored_sources(tallies))
    ms = tallies.get("ms_buildings") or {}
    usa = tallies.get("usa_structures") or {}
    critical = tallies.get("critical_facilities") or {}

    hospitals_clean = _clean(tallies.get("hospitals"))
    schools_clean = _clean(tallies.get("schools"))
    lines_clean = _clean(tallies.get("transmission"))

    usa_ok = "usa_structures" not in errored and isinstance(usa, dict)
    ms_ok = "ms_buildings" not in errored and isinstance(ms, dict)
    by_occ = (usa.get("by_occupancy") or {}) if usa_ok else {}

    if ms_ok:
        structures_total: int | None = int(ms.get("count", 0) or 0)
    elif usa_ok:
        structures_total = int(usa.get("total", 0) or 0)
    else:
        structures_total = None

    def _occ(name: str) -> int | None:
        return int(by_occ.get(name, 0) or 0) if usa_ok else None

    residential = _occ("Residential")
    population, pop_method, pop_note = _population(residential, polygon_wkt)

    # Named facilities — included in the payload so downstream agents and
    # the Master IC can reference specific places by name instead of
    # quoting only aggregate counts. Sort by capacity (beds/enrollment) so
    # the most-consequential first; cap each list at 5 to keep the prompt
    # context small.
    named_hospitals = sorted(
        (
            {
                "name": str(h.get("name") or "Unknown"),
                "beds": int(h.get("beds", 0) or 0),
                "lat": float(h.get("lat", 0.0) or 0.0),
                "lon": float(h.get("lon", 0.0) or 0.0),
            }
            for h in hospitals_clean
            if h.get("name") and h.get("name") != "Unknown"
        ),
        key=lambda h: -h["beds"],
    )[:5]
    named_schools = sorted(
        (
            {
                "name": str(s.get("name") or "Unknown"),
                "enrollment": int(s.get("enrollment", 0) or 0),
                "lat": float(s.get("lat", 0.0) or 0.0),
                "lon": float(s.get("lon", 0.0) or 0.0),
            }
            for s in schools_clean
            if s.get("name") and s.get("name") != "Unknown"
        ),
        key=lambda s: -s["enrollment"],
    )[:5]

    hosp_ok = "hospitals" not in errored
    school_ok = "schools" not in errored
    lines_ok = "transmission" not in errored
    crit_sub_errors = (critical.get("errors") or {}) if isinstance(critical, dict) else {}
    crit_all_failed = isinstance(critical, dict) and (
        bool(critical.get("error")) or len(crit_sub_errors) >= 3
    )

    return {
        "structures_total": structures_total,
        "total_footprint_sqm": (
            float(ms.get("total_footprint_sqm", 0.0) or 0.0) if ms_ok else None
        ),
        "structures_by_occupancy": by_occ,
        "residential_count": residential,
        "commercial_count": _occ("Commercial"),
        "public_count": _occ("Public"),
        "industrial_count": _occ("Industrial"),
        "population_estimate": population,
        "population_method": pop_method,
        "population_note": pop_note,
        "hospitals_count": len(hospitals_clean) if hosp_ok else None,
        "hospitals_total_beds": (
            sum(int(h.get("beds", 0) or 0) for h in hospitals_clean) if hosp_ok else None
        ),
        "named_hospitals": named_hospitals,
        "schools_count": len(schools_clean) if school_ok else None,
        "schools_total_enrollment": (
            sum(int(s.get("enrollment", 0) or 0) for s in schools_clean)
            if school_ok
            else None
        ),
        "named_schools": named_schools,
        "transmission_segments": len(lines_clean) if lines_ok else None,
        "transmission_max_kv": (
            max((float(ln.get("voltage_kv", 0) or 0) for ln in lines_clean), default=0.0)
            if lines_ok
            else None
        ),
        "critical_facilities_total": (
            None
            if crit_all_failed
            else int(critical.get("total", 0) or 0)
            if isinstance(critical, dict)
            else None
        ),
        "critical_facilities_partial": sorted(crit_sub_errors),
        "unavailable": sorted(errored),
    }


def fmt_count(value: Any, unit: str = "") -> str:
    """'1,234 residences' or 'residences unavailable' for a None count."""
    if value is None:
        return f"{unit} unavailable".strip() if unit else "unavailable"
    try:
        text = f"{int(value):,}"
    except (TypeError, ValueError):
        return f"{unit} unavailable".strip() if unit else "unavailable"
    return f"{text} {unit}".strip()


def population_phrase(roll: dict[str, Any]) -> str:
    pop = roll.get("population_estimate")
    if pop is None:
        return "population unavailable"
    if roll.get("population_method") == "area_density_estimate":
        return f"~{pop:,} people (rough area-density estimate; no structure data)"
    return f"~{pop:,} people (from counted residences)"


def _key_findings(roll: dict[str, Any]) -> list[str]:
    findings = []
    if roll.get("unavailable"):
        findings.append(
            "PARTIAL inventory — unavailable: " + ", ".join(roll["unavailable"])
        )
    kv = roll.get("transmission_max_kv")
    findings += [
        population_phrase(roll),
        (
            f"{fmt_count(roll['residential_count'], 'residential structures')} "
            f"({fmt_count(roll['structures_total'], 'total in cone')})"
        ),
        (
            f"{fmt_count(roll['hospitals_count'], 'hospitals')} "
            f"({fmt_count(roll['hospitals_total_beds'], 'beds')})"
        ),
        (
            f"{fmt_count(roll['schools_count'], 'schools')} "
            f"({fmt_count(roll['schools_total_enrollment'], 'enrollment')})"
        ),
        (
            f"{fmt_count(roll['transmission_segments'], 'transmission segments')}"
            + (f" (max {kv:.0f} kV)" if kv is not None else "")
        ),
        fmt_count(roll["critical_facilities_total"], "critical facilities"),
    ]
    return findings


# --------------------------------------------------------------------------- #
# LLM narrative (Haiku 4.5, with hermetic fallback)
# --------------------------------------------------------------------------- #


def _load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "Inventory values-at-risk. RECOMMEND only."


def _fallback_narrative(
    roll: dict[str, Any], cone_source: str, incident: Incident | None
) -> str:
    inc = incident.name if incident else "the incident"
    # Surface named facilities so the IC sees specific places, not just counts.
    named_h = roll.get("named_hospitals") or []
    named_s = roll.get("named_schools") or []
    hosp_phrase = (
        f"{fmt_count(roll['hospitals_count'], 'hospitals')} "
        f"({fmt_count(roll['hospitals_total_beds'], 'beds')}"
        + (
            "; highest-capacity: "
            + ", ".join(f"{h['name']} ({h['beds']} beds)" for h in named_h[:3])
            if named_h
            else ""
        )
        + ")"
    )
    school_phrase = (
        f"{fmt_count(roll['schools_count'], 'schools')} "
        f"({fmt_count(roll['schools_total_enrollment'], 'enrollment')}"
        + (
            "; largest: " + ", ".join(f"{s['name']}" for s in named_s[:3])
            if named_s
            else ""
        )
        + ")"
    )
    caveat = ""
    if roll.get("unavailable"):
        caveat = (
            "PARTIAL inventory: "
            + ", ".join(roll["unavailable"])
            + " did not respond, so those counts are unknown, not zero. "
        )
    kv = roll.get("transmission_max_kv")
    return (
        f"{caveat}Values-at-Risk inventory for {inc} (cone source: {cone_source}): "
        f"{population_phrase(roll)}. "
        f"PROPOSE prioritizing protection of "
        f"{fmt_count(roll['residential_count'], 'residential structures')}, "
        f"{hosp_phrase}, and {school_phrase}. "
        f"{fmt_count(roll['transmission_segments'], 'transmission segments')}"
        + (f" (up to {kv:.0f} kV)" if kv is not None else "")
        + f" and {fmt_count(roll['critical_facilities_total'], 'critical facilities')} "
        "are exposed. RECOMMEND coordinating with utility owners on "
        "de-energization and verifying CMS hospital evacuation plans."
    )


def _confidence(tallies: dict[str, Any], roll: dict[str, Any]) -> tuple[float, str]:
    """0.9 minus 0.1 per failed layer, with two caps.

    * A core layer (buildings / occupancy) failed: the headline structure and
      population numbers are unknown, so cap at 0.5.
    * Any layer failed AND everything that did answer is zero: an empty
      result built on a partial picture is not an all-clear, so cap at 0.4.
    """
    errored = errored_sources(tallies)
    confidence = max(0.1, 0.9 - 0.1 * len(errored))
    if not errored:
        return confidence, (
            "all 6 data sources returned; counts are for the cone polygon "
            "(USA Structures / MS Building Footprints)"
        )
    drivers = [f"{len(errored)}/6 data sources errored ({', '.join(errored)})"]
    if CORE_SOURCES & set(errored):
        confidence = min(confidence, 0.5)
        drivers.append("structure counts unknown")
    counts = [
        roll.get(k)
        for k in (
            "structures_total",
            "residential_count",
            "hospitals_count",
            "schools_count",
            "transmission_segments",
            "critical_facilities_total",
        )
    ]
    if all(not c for c in counts if c is not None):
        confidence = min(confidence, 0.4)
        drivers.append("zero counts from a partial picture are not an all-clear")
    return confidence, "; ".join(drivers)


async def _llm_narrative(
    roll: dict[str, Any], cone_source: str, incident: Incident | None
) -> tuple[str, bool]:
    """(narrative, written_by_llm)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_narrative(roll, cone_source, incident), False
    try:
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return _fallback_narrative(roll, cone_source, incident), False

    system_prompt = _load_prompt()
    inc_name = incident.name if incident else "unknown incident"
    user_msg = (
        f"Incident: {inc_name}\n"
        f"Cone source: {cone_source}\n"
        f"Tally JSON: {roll}\n\n"
        "Write a 3-5 sentence IMT-style summary of the values at risk. "
        "Use PROPOSE / RECOMMEND verbs only. No fabricated numbers. "
        "When `named_hospitals` or `named_schools` are present, reference "
        "the highest-capacity 1-2 facilities BY NAME so the IC knows which "
        "specific places need protection — don't just cite aggregate counts."
    )
    try:
        from ..tools.llm_stream import stream_text  # noqa: PLC0415
        llm = ChatAnthropic(model=LLM_MODEL_ID, max_tokens=400, temperature=0.2, timeout=30)
        text = await stream_text(
            llm,
            [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)],
        )
        if text and text.strip():
            # The LLM can drop the "PARTIAL inventory" caveat; keep it.
            caveat = "PARTIAL inventory" if roll.get("unavailable") else ""
            if caveat and caveat not in text:
                text = (
                    "PARTIAL inventory: " + ", ".join(roll["unavailable"])
                    + " did not respond, so those counts are unknown, not zero. "
                    + text
                )
            return text.strip(), True
    except Exception as exc:  # noqa: BLE001 — fall back to deterministic text
        log.warning("values_at_risk LLM narrative failed: %s", exc)
    return _fallback_narrative(roll, cone_source, incident), False


# --------------------------------------------------------------------------- #
# Citations
# --------------------------------------------------------------------------- #


def _citations(tallies: dict[str, Any], used_llm: bool = False) -> CitationBundle:
    """Build the CitationBundle from the actual responses we got."""
    errors = {k: _has_error(v) for k, v in tallies.items()}
    datasets = [
        Dataset(
            name="Microsoft Building Footprints (via FEMA USA Structures)",
            version="USA Structures v2",
            url=(
                "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/"
                "services/USA_Structures_View/FeatureServer/0"
            ),
            timestamp="2024-Q4",
        ),
        Dataset(
            name="FEMA USA Structures (occupancy class)",
            version="v2",
            url=(
                "https://gis-fema.hub.arcgis.com/datasets/"
                "fema::usa-structures/about"
            ),
        ),
        Dataset(
            name="HIFLD Open / CMS Provider of Services — Hospitals",
            url="https://hifld-geoplatform.opendata.arcgis.com/",
        ),
        Dataset(
            name="NCES Common Core of Data — Public Schools",
            url="https://nces.ed.gov/ccd/",
        ),
        Dataset(
            name="EIA US Electric Power Transmission Lines",
            url=(
                "https://atlas.eia.gov/datasets/"
                "electric-power-transmission-lines"
            ),
        ),
        Dataset(
            name="HIFLD critical facilities (fire stations, EOCs, comm towers)",
            url="https://hifld-geoplatform.opendata.arcgis.com/",
        ),
    ]
    for ds, errored_key in zip(
        datasets,
        [
            "ms_buildings",
            "usa_structures",
            "hospitals",
            "schools",
            "transmission",
            "critical_facilities",
        ],
        strict=True,
    ):
        if errors.get(errored_key):
            ds.version = (ds.version or "") + " (errored)"
    return CitationBundle(
        datasets=datasets,
        models=[narrator_model(LLM_MODEL_ID, used_llm)],
        reasoning_trace_id=str(uuid.uuid4()),
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict:
    cone, cone_source = _spread_cone(state)
    polygon_wkt = _cone_to_wkt(cone, state.incident)
    try:
        bbox = _bbox_from_wkt(polygon_wkt)
    except Exception:  # noqa: BLE001
        # Fall back to a tight bbox around the incident (or zeros).
        if state.incident is not None:
            d = DEFAULT_RADIUS_KM / 110.0
            bbox = (
                state.incident.lon - d,
                state.incident.lat - d,
                state.incident.lon + d,
                state.incident.lat + d,
            )
        else:
            bbox = (-0.1, -0.1, 0.1, 0.1)

    tallies = await _gather_tallies(polygon_wkt)
    roll = _rollup(tallies, polygon_wkt)
    narrative, used_llm = await _llm_narrative(roll, cone_source, state.incident)
    findings = _key_findings(roll)
    confidence, driver = _confidence(tallies, roll)

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload={
            "cone_source": cone_source,
            "polygon_wkt": polygon_wkt,
            "bbox": bbox,
            "rollup": roll,
            "key_findings": findings,
            "tallies": tallies,
        },
        confidence=confidence,
        confidence_driver=driver,
        citation_bundle=_citations(tallies, used_llm),
    )
    return {"outputs": {AGENT_NAME: output}}


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _los_padres_fixture() -> AgentState:
    """Synthetic Los Padres NF incident used by the smoke test."""
    return AgentState(
        incident=Incident(
            id="LPNF-SMOKE-001",
            name="Los Padres NF Smoke Test",
            lat=34.5246,
            lon=-119.7613,
            acres=120.0,
            contained_pct=0.0,
            source="synthetic",
        ),
        operational_period=1,
        user_query="What is exposed in the 24-hour spread cone?",
    )


async def _smoke() -> None:
    state = _los_padres_fixture()
    patch = await run(state)
    output: AgentOutput = patch["outputs"][AGENT_NAME]
    print(f"agent           : {output.agent}")
    print(f"confidence      : {output.confidence}")
    print(f"confidence_drv  : {output.confidence_driver}")
    print(f"cone_source     : {output.payload['cone_source']}")
    print(f"bbox            : {output.payload['bbox']}")
    print("key_findings    :")
    for finding in output.payload["key_findings"]:
        print(f"  - {finding}")
    print(f"narrative       : {output.narrative}")
    print(f"datasets cited  : {len(output.citation_bundle.datasets)}")


if __name__ == "__main__":
    asyncio.run(_smoke())
