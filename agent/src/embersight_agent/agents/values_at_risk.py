"""Values-at-Risk subagent.

Pass-2: spatial-join MS Building Footprints + USA Structures + CMS
hospitals + NCES schools + EIA transmission lines against the predicted
spread cone (24h bucket from spread_simulation, or a 10 km default
radius around the incident when spread output is not present yet).

The LLM (Haiku 4.5) is given a fused tally and the prompt at
``prompts/values_at_risk.md`` and must produce a single narrative plus a
bullet list of headline findings. If ``ANTHROPIC_API_KEY`` is missing
we fall back to a deterministic templated narrative so the smoke test
remains hermetic.

No ``interrupt()`` is requested here — values-at-risk is informational.
"""

from __future__ import annotations

import asyncio
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
from ..tools.infra import (
    query_critical_facilities,
    query_hospitals,
    query_schools,
    query_transmission_lines,
)

AGENT_NAME = "values_at_risk"

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "values_at_risk.md"
)

DEFAULT_RADIUS_KM = 10.0
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
    """Return (cone_payload, source_label)."""
    spread = state.outputs.get("spread_simulation")
    if spread is not None:
        cones = (spread.payload or {}).get("cones") or {}
        cone_24h = cones.get("24h")
        if cone_24h:
            return cone_24h, "spread_simulation.24h"
    return None, f"default-radius-{DEFAULT_RADIUS_KM:.0f}km"


# --------------------------------------------------------------------------- #
# Tool fan-out
# --------------------------------------------------------------------------- #


async def _gather_tallies(
    polygon_wkt: str, bbox: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Run the five spatial queries in parallel (each on a worker thread
    because the underlying HTTPX calls are synchronous)."""
    loop = asyncio.get_running_loop()
    coros = {
        "ms_buildings": loop.run_in_executor(None, query_ms_buildings, polygon_wkt),
        "usa_structures": loop.run_in_executor(
            None, query_usa_structures, polygon_wkt
        ),
        "hospitals": loop.run_in_executor(None, query_hospitals, bbox),
        "schools": loop.run_in_executor(None, query_schools, bbox),
        "transmission": loop.run_in_executor(None, query_transmission_lines, bbox),
        "critical_facilities": loop.run_in_executor(
            None, query_critical_facilities, bbox
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
    if isinstance(result, dict) and result.get("error"):
        return True
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return bool(result[0].get("error"))
    return False


# --------------------------------------------------------------------------- #
# Tally rollup
# --------------------------------------------------------------------------- #


def _rollup(tallies: dict[str, Any]) -> dict[str, Any]:
    ms = tallies.get("ms_buildings") or {}
    usa = tallies.get("usa_structures") or {}
    hospitals = tallies.get("hospitals") or []
    schools = tallies.get("schools") or []
    transmission = tallies.get("transmission") or []
    critical = tallies.get("critical_facilities") or {}

    hospitals_clean = [
        h for h in hospitals if isinstance(h, dict) and "error" not in h
    ]
    schools_clean = [
        s for s in schools if isinstance(s, dict) and "error" not in s
    ]
    lines_clean = [
        ln for ln in transmission if isinstance(ln, dict) and "error" not in ln
    ]

    by_occ = (usa.get("by_occupancy") or {}) if isinstance(usa, dict) else {}

    return {
        "structures_total": ms.get("count", 0) if isinstance(ms, dict) else 0,
        "total_footprint_sqm": (
            ms.get("total_footprint_sqm", 0.0) if isinstance(ms, dict) else 0.0
        ),
        "structures_by_occupancy": by_occ,
        "residential_count": by_occ.get("Residential", 0),
        "commercial_count": by_occ.get("Commercial", 0),
        "public_count": by_occ.get("Public", 0),
        "industrial_count": by_occ.get("Industrial", 0),
        "hospitals_count": len(hospitals_clean),
        "hospitals_total_beds": sum(int(h.get("beds", 0)) for h in hospitals_clean),
        "schools_count": len(schools_clean),
        "schools_total_enrollment": sum(
            int(s.get("enrollment", 0)) for s in schools_clean
        ),
        "transmission_segments": len(lines_clean),
        "transmission_max_kv": max(
            (float(ln.get("voltage_kv", 0) or 0) for ln in lines_clean),
            default=0.0,
        ),
        "critical_facilities_total": (
            critical.get("total", 0) if isinstance(critical, dict) else 0
        ),
    }


def _key_findings(roll: dict[str, Any]) -> list[str]:
    return [
        (
            f"{roll['residential_count']:,} residential structures "
            f"({roll['structures_total']:,} total in cone)"
        ),
        (
            f"{roll['hospitals_count']} hospitals "
            f"({roll['hospitals_total_beds']:,} beds)"
        ),
        (
            f"{roll['schools_count']} schools "
            f"({roll['schools_total_enrollment']:,} enrollment)"
        ),
        (
            f"{roll['transmission_segments']} transmission segments "
            f"(max {roll['transmission_max_kv']:.0f} kV)"
        ),
        f"{roll['critical_facilities_total']} critical facilities",
    ]


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
    return (
        f"Values-at-Risk inventory for {inc} (cone source: {cone_source}). "
        f"PROPOSE prioritizing protection of "
        f"{roll['residential_count']:,} residential structures, "
        f"{roll['hospitals_count']} hospitals "
        f"({roll['hospitals_total_beds']:,} beds), and "
        f"{roll['schools_count']} schools "
        f"({roll['schools_total_enrollment']:,} enrollment). "
        f"{roll['transmission_segments']} transmission segments "
        f"(up to {roll['transmission_max_kv']:.0f} kV) "
        f"and {roll['critical_facilities_total']} critical facilities "
        "are exposed. RECOMMEND coordinating with utility owners on "
        "de-energization and verifying CMS hospital evacuation plans."
    )


async def _llm_narrative(
    roll: dict[str, Any], cone_source: str, incident: Incident | None
) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_narrative(roll, cone_source, incident)
    try:
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return _fallback_narrative(roll, cone_source, incident)

    system_prompt = _load_prompt()
    inc_name = incident.name if incident else "unknown incident"
    user_msg = (
        f"Incident: {inc_name}\n"
        f"Cone source: {cone_source}\n"
        f"Tally JSON: {roll}\n\n"
        "Write a 3-5 sentence IMT-style summary of the values at risk. "
        "Use PROPOSE / RECOMMEND verbs only. No fabricated numbers."
    )
    try:
        from ..tools.llm_stream import stream_text  # noqa: PLC0415
        llm = ChatAnthropic(model=LLM_MODEL_ID, max_tokens=400, temperature=0.2)
        text = await stream_text(
            llm,
            [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)],
        )
        if text and text.strip():
            return text.strip()
    except Exception:  # noqa: BLE001
        pass
    return _fallback_narrative(roll, cone_source, incident)


# --------------------------------------------------------------------------- #
# Citations
# --------------------------------------------------------------------------- #


def _citations(tallies: dict[str, Any]) -> CitationBundle:
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
        models=[Model(name=LLM_MODEL_ID, version="2025-10-01")],
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

    tallies = await _gather_tallies(polygon_wkt, bbox)
    roll = _rollup(tallies)
    narrative = await _llm_narrative(roll, cone_source, state.incident)
    findings = _key_findings(roll)

    errors = sum(1 for v in tallies.values() if _has_error(v))
    confidence = max(0.1, 0.9 - 0.1 * errors)
    if errors:
        driver = (
            f"{errors}/6 data sources errored — "
            "confidence reduced 0.1 per source"
        )
    else:
        driver = "all 6 data sources returned; MS Building Footprints vintage 2024-Q4"

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
        citation_bundle=_citations(tallies),
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
