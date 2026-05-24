"""Spread Simulation subagent — Pass-2.

Pyretechnics surface-fire ROS + Anderson elliptical cone + Monte Carlo N=200
perturbing wind speed/direction and fuel moisture.

Outputs 1h / 6h / 12h / 24h probability-of-burn polygons and interrupts on
trigger-point violations.
"""

from __future__ import annotations

import asyncio
import math
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

from ..hitl import audit_entry, request_human_decision
from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model

# Average residents per residential structure — same default the evac tool uses
# so the population number on the cone matches the evac intel agent's math.
_HOUSEHOLD_SIZE = 2.5

AGENT_NAME = "spread_simulation"
_PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "spread_simulation.md"

# Default dead-fuel moisture (%) used when weather subagent provides no RAWS data.
_DEFAULT_FM = {
    "1h": 6.0,
    "10h": 8.0,
    "100h": 10.0,
    "live_herb": 60.0,
    "live_woody": 90.0,
}


# --------------------------------------------------------------------------- #
# Lazy imports
# --------------------------------------------------------------------------- #


def _spread_tools():
    from ..tools.pyretechnics_spread import (
        anderson_lwr_from_wind,
        detect_trigger_breach,
        monte_carlo_cone,
        polygon_local_to_geojson,
        lonlat_to_local,
        pyretechnics_available,
        PYRETECHNICS_VERSION,
    )

    return {
        "anderson_lwr_from_wind": anderson_lwr_from_wind,
        "detect_trigger_breach": detect_trigger_breach,
        "monte_carlo_cone": monte_carlo_cone,
        "polygon_local_to_geojson": polygon_local_to_geojson,
        "lonlat_to_local": lonlat_to_local,
        "pyretechnics_available": pyretechnics_available,
        "PYRETECHNICS_VERSION": PYRETECHNICS_VERSION,
    }


def _np():
    import numpy as np

    return np


# --------------------------------------------------------------------------- #
# LLM narration (graceful degradation when API key absent)
# --------------------------------------------------------------------------- #


async def _llm_narrate(context: str) -> str | None:
    """Call claude-sonnet-4-5 for narrative + critical concerns.
    Returns None if no API key is configured."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        raw_model = os.environ.get("EMBERSIGHT_MODEL_SPREAD", "anthropic:claude-sonnet-4-5")
        model_name = raw_model.split(":", 1)[1] if ":" in raw_model else raw_model

        prompt_text = ""
        if _PROMPT_PATH.exists():
            prompt_text = _PROMPT_PATH.read_text()

        from ..tools.llm_stream import stream_text
        llm = ChatAnthropic(model=model_name, max_tokens=800, temperature=0.2)
        msgs = [
            SystemMessage(content=prompt_text or "You are a wildland fire analyst."),
            HumanMessage(content=context),
        ]
        return await stream_text(llm, msgs)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Input extraction helpers
# --------------------------------------------------------------------------- #


def _extract_weather(weather_output: AgentOutput | None) -> dict:
    """Pull wind speed, wind direction, and RAWS fuel moisture from weather payload.

    Preferred source is ``payload.critical_window`` (the worst HRRR hour in the
    24-h window, which is the right hour to drive a spread cone). Falls back to
    the flat top-level ``wind_speed_mph`` / ``wind_dir_deg`` keys when the
    critical window is unavailable.

    Both sources report wind direction in meteorological "FROM" convention. The
    returned ``wind_dir_deg`` preserves that convention; the caller flips it by
    180° before feeding pyretechnics, which expects fire-head "TO" direction.
    """
    if weather_output is None:
        return {}
    p = weather_output.payload or {}
    cw = p.get("critical_window") or {}

    wind_speed = cw.get("wind_speed_mph")
    if wind_speed is None:
        wind_speed = p.get("wind_speed_mph", 15.0)

    # critical_window uses HRRR's wind_direction_deg; flat path uses wind_dir_deg.
    wind_dir = cw.get("wind_direction_deg")
    if wind_dir is None:
        wind_dir = p.get("wind_dir_deg", 270.0)

    return {
        "wind_speed_mph": float(wind_speed),
        "wind_dir_deg": float(wind_dir),  # MET "FROM" convention
        "fuel_moisture": p.get("fuel_moisture", {}),
        "rh_pct": float(cw.get("rh_pct", p.get("rh_pct", 20.0))),
        "temp_f": float(cw.get("temp_f", p.get("temp_f", 90.0))),
        "_source": "critical_window" if cw else "flat",
    }


def _extract_terrain_fuel(terrain_output: AgentOutput | None) -> dict:
    """Pull fuel model, slope, and aspect from terrain payload.

    ``fuel_model`` may arrive in three shapes — handled in order:
      1. Dict with ``dominant_classes: [{code, fraction}, ...]`` (top-rank wins).
      2. Plain FBFM40 code string (the flat-projection contract).
      3. Missing — fall back to ``payload.fuel_detail.dominant_classes``
         (the rich LANDFIRE structure terrain_fuel publishes alongside the
         flat code).
    """
    if terrain_output is None:
        return {}
    p = terrain_output.payload or {}

    code: str | None = None
    fuel_field = p.get("fuel_model")
    if isinstance(fuel_field, dict):
        classes = fuel_field.get("dominant_classes") or []
        if classes:
            code = classes[0].get("code")
    elif isinstance(fuel_field, str) and fuel_field:
        code = fuel_field
    if not code:
        detail = p.get("fuel_detail") or {}
        classes = detail.get("dominant_classes") or []
        if classes:
            code = classes[0].get("code")

    return {
        "fuel_model": code or "GS2",
        "slope_pct": float(p.get("slope_pct", 15.0)),
        "aspect_deg": float(p.get("aspect_deg", 225.0)),
    }


def _fuel_moisture(weather: dict) -> dict:
    """Merge RAWS-derived moisture with defaults."""
    raws = weather.get("fuel_moisture", {}) or {}
    fm = dict(_DEFAULT_FM)
    for k in ("1h", "10h", "100h", "live_herb", "live_woody"):
        if k in raws:
            try:
                fm[k] = float(raws[k])
            except (ValueError, TypeError):
                pass
    return fm


def _trigger_points(state: AgentState) -> list:
    """Retrieve trigger-point (lat, lon) tuples from state.

    The Incident model does not formally carry trigger_points; callers can
    pass them via the raw dict or the scratch pad.
    """
    raw_pts: list | None = None
    if state.incident is not None:
        raw_pts = (
            getattr(state.incident, "trigger_points", None)
            or state.incident.raw.get("trigger_points")
        )
    if not raw_pts:
        raw_pts = state.scratch.get("trigger_points")
    return raw_pts or []


# --------------------------------------------------------------------------- #
# Cone → GeoJSON serialisation
# --------------------------------------------------------------------------- #


def _cones_to_geojson(
    mc_result: dict,
    lat0: float,
    lon0: float,
    polygon_local_to_geojson_fn: Any,
) -> tuple[dict, dict]:
    """Convert monte_carlo_cone output to two parallel dicts:

    - ``cones`` — primary, ``{"1h": gj_p50, "6h": gj_p50, "12h": gj_p50, "24h": gj_p50}``.
      One GeoJSON polygon per horizon (the p50 ensemble band). This is the
      canonical "expected cone" consumed by ``values_at_risk`` and
      ``evacuation_intelligence`` for spatial intersection.
    - ``cone_bands`` — detail, ``{"1h": {"p25": gj, "p50": gj, "p75": gj, "p95": gj}, ...}``.
      Full quantile bands for richer UI rendering (uncertainty fans).

    Keys are strings ("1h", "6h", "12h", "24h") because every downstream
    consumer reads string keys.
    """
    cones: dict = {}
    bands_out: dict = {}
    for h in (1, 6, 12, 24):
        key = f"{h}h"
        entry = mc_result.get(h)
        if entry is None:
            cones[key] = None
            bands_out[key] = {"p25": None, "p50": None, "p75": None, "p95": None}
            continue
        raw_bands = entry.get("bands", [None, None, None, None])
        gj_bands = [polygon_local_to_geojson_fn(b, lat0, lon0) for b in raw_bands]
        # raw_bands is [p25, p50, p75, p95]; canonical cone = p50.
        cones[key] = gj_bands[1] if len(gj_bands) > 1 else None
        bands_out[key] = {
            "p25": gj_bands[0] if len(gj_bands) > 0 else None,
            "p50": gj_bands[1] if len(gj_bands) > 1 else None,
            "p75": gj_bands[2] if len(gj_bands) > 2 else None,
            "p95": gj_bands[3] if len(gj_bands) > 3 else None,
        }
    return cones, bands_out


# --------------------------------------------------------------------------- #
# High-risk zone derivation (from cone, sans Cal OES Zonehaven cross-ref)
# --------------------------------------------------------------------------- #


_CARDINALS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _bearing_to_cardinal(deg: float) -> str:
    """8-point compass bin. 0° = N, 90° = E, increasing clockwise."""
    idx = int(((deg + 22.5) % 360.0) // 45.0)
    return _CARDINALS[idx]


def _high_risk_zones(mc_result: dict) -> list[dict]:
    """Derive high-risk sectors from the p75 Monte-Carlo band per horizon.

    Without a Cal OES Zonehaven polygon set loaded, we cannot publish real
    zone IDs; this is a sector descriptor good enough for the IC dashboard
    and master_ic headline routing. Each entry names the cardinal heading
    of the p75 band's centroid, distance from ignition, projected area,
    and horizon — i.e. "where the fire is most likely to be in N hours".
    """
    zones: list[dict] = []
    for h in (6, 12, 24):
        entry = mc_result.get(h)
        if not entry:
            continue
        bands = entry.get("bands", [])
        if len(bands) < 3:
            continue
        p75 = bands[2]
        if p75 is None or p75.is_empty:
            continue
        cx, cy = p75.centroid.x, p75.centroid.y
        # Convert local (east=x, north=y) centroid into compass bearing.
        bearing = (math.degrees(math.atan2(cx, cy)) + 360.0) % 360.0
        distance_km = math.hypot(cx, cy) / 1000.0
        area_km2 = p75.area / 1_000_000.0
        sector = _bearing_to_cardinal(bearing)
        zones.append(
            {
                "id": f"head-{h}h-{sector}",
                "sector": sector,
                "bearing_deg": round(bearing, 1),
                "distance_km": round(distance_km, 2),
                "horizon_h": h,
                "probability_min": 0.75,
                "area_km2": round(area_km2, 2),
            }
        )
    return zones


# --------------------------------------------------------------------------- #
# Confidence from MC ensemble
# --------------------------------------------------------------------------- #


def _mc_confidence(mc_result: dict, horizon: int = 24) -> tuple[float, str]:
    """1 - (std/mean) of polygon areas at the requested horizon, clamped [0,1]."""
    np = _np()
    entry = mc_result.get(horizon)
    if entry is None:
        return 0.40, "missing 24 h horizon"
    areas = [a for a in entry.get("areas_m2", []) if a > 0]
    if not areas:
        return 0.45, "zero-area ellipses (calm / wet conditions)"
    arr = np.array(areas)
    mean_a = float(np.mean(arr))
    std_a = float(np.std(arr))
    cv = std_a / mean_a if mean_a > 0 else 1.0
    confidence = max(0.0, min(1.0, 1.0 - cv))
    if cv < 0.10:
        driver = "low ensemble spread — high confidence in spread trajectory"
    elif cv < 0.25:
        driver = "moderate directional uncertainty"
    else:
        driver = "high directional/moisture uncertainty — wide spread cone"
    return confidence, driver


# --------------------------------------------------------------------------- #
# Cone-impact: population + critical infrastructure inside the 24h cone
# --------------------------------------------------------------------------- #


def _cone_geojson_to_wkt(cone_geojson: dict | None) -> str | None:
    """Convert the 24h cone GeoJSON polygon to a WKT string for spatial joins."""
    if not cone_geojson:
        return None
    try:
        from shapely.geometry import shape  # noqa: PLC0415

        return shape(cone_geojson).wkt
    except Exception:  # noqa: BLE001
        return None


def _incident_irwin_id(incident) -> str | None:  # noqa: ANN001 — pydantic Incident
    """Pull a WFIGS IrwinID off the incident if one is exposed.

    Frontend prefixes WFIGS ids with ``wfigs:``; the agent's Incident may
    surface the same id directly or stash the raw id under ``raw``.
    """
    if incident is None:
        return None
    inc_id = getattr(incident, "id", "") or ""
    if isinstance(inc_id, str) and inc_id.startswith("wfigs:"):
        return inc_id[6:]
    raw = getattr(incident, "raw", None) or {}
    for k in ("irwin_id", "IrwinID", "poly_IRWINID", "IRWINID"):
        v = raw.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _build_swept_cone(cone_geojson: dict | None, perimeter_geom, incident) -> dict | None:  # noqa: ANN001
    """Minkowski-sweep the cone over the fire perimeter via shapely union.

    For each sampled perimeter vertex, translate a copy of the cone so its
    rear vertex (at the incident point in the agent's local frame) lands on
    that vertex, then ``unary_union`` everything into a single multipolygon.
    The result is the current fire footprint physically extended in the
    spread direction — the same shape rendered on the map — and the polygon
    we run impact queries against. Falls back to the bare cone when anything
    upstream is missing.
    """
    if not cone_geojson or perimeter_geom is None or incident is None:
        return cone_geojson
    try:
        from shapely.affinity import translate  # noqa: PLC0415
        from shapely.geometry import mapping, shape  # noqa: PLC0415
        from shapely.ops import unary_union  # noqa: PLC0415

        cone_poly = shape(cone_geojson)
        if cone_poly.is_empty:
            return cone_geojson

        inc_lon = float(getattr(incident, "lon", 0.0))
        inc_lat = float(getattr(incident, "lat", 0.0))

        def _ring_points(geom):  # noqa: ANN001
            if geom.geom_type == "Polygon":
                yield from geom.exterior.coords
                for r in geom.interiors:
                    yield from r.coords
            elif geom.geom_type == "MultiPolygon":
                for p in geom.geoms:
                    yield from _ring_points(p)

        perim_pts = [(x, y) for x, y in _ring_points(perimeter_geom)]
        if not perim_pts:
            return cone_geojson

        # Cap vertex count so union work stays bounded for dense WFIGS polys.
        MAX_PERIM = 96
        stride = max(1, len(perim_pts) // MAX_PERIM)
        sampled = perim_pts[::stride]

        swept_polys = [perimeter_geom]
        for px, py in sampled:
            dx = px - inc_lon
            dy = py - inc_lat
            swept_polys.append(translate(cone_poly, xoff=dx, yoff=dy))

        merged = unary_union(swept_polys)
        if merged.is_empty:
            return cone_geojson
        return mapping(merged)
    except Exception:  # noqa: BLE001
        return cone_geojson


def _wkt_bbox(polygon_wkt: str) -> tuple[float, float, float, float] | None:
    try:
        from shapely import wkt  # noqa: PLC0415

        geom = wkt.loads(polygon_wkt)
        minx, miny, maxx, maxy = geom.bounds
        return float(minx), float(miny), float(maxx), float(maxy)
    except Exception:  # noqa: BLE001
        return None


async def _gather_cone_impact(polygon_wkt: str, bbox: tuple[float, float, float, float]) -> dict:
    """Run all six spatial queries in parallel and roll them up.

    Mirrors the values_at_risk fan-out but is computed against the actual
    spread cone the agent just produced so the cone label is consistent
    with the geometry it describes.
    """
    from ..tools.buildings import query_ms_buildings, query_usa_structures
    from ..tools.evac import estimate_population
    from ..tools.infra import (
        query_critical_facilities,
        query_hospitals,
        query_schools,
        query_transmission_lines,
    )

    loop = asyncio.get_running_loop()
    tasks = {
        "ms_buildings": loop.run_in_executor(None, query_ms_buildings, polygon_wkt),
        "usa_structures": loop.run_in_executor(None, query_usa_structures, polygon_wkt),
        "hospitals": loop.run_in_executor(None, query_hospitals, bbox),
        "schools": loop.run_in_executor(None, query_schools, bbox),
        "transmission": loop.run_in_executor(None, query_transmission_lines, bbox),
        "critical_facilities": loop.run_in_executor(None, query_critical_facilities, bbox),
        "population_est": loop.run_in_executor(None, estimate_population, polygon_wkt),
    }
    raw: dict[str, Any] = {}
    for key, fut in tasks.items():
        try:
            raw[key] = await fut
        except Exception as exc:  # noqa: BLE001
            raw[key] = {"error": f"task:{exc}"}

    ms = raw.get("ms_buildings") or {}
    usa = raw.get("usa_structures") or {}
    hospitals = [h for h in (raw.get("hospitals") or []) if isinstance(h, dict) and "error" not in h]
    schools = [s for s in (raw.get("schools") or []) if isinstance(s, dict) and "error" not in s]
    lines = [ln for ln in (raw.get("transmission") or []) if isinstance(ln, dict) and "error" not in ln]
    critical = raw.get("critical_facilities") or {}
    by_occ = (usa.get("by_occupancy") or {}) if isinstance(usa, dict) else {}

    residential = int(by_occ.get("Residential", 0) or 0)
    # Two population estimators; take the larger as a defensible upper bound for
    # the cone label (footprint-density vs. residential-occupancy * household).
    pop_from_residential = int(round(residential * _HOUSEHOLD_SIZE))
    pop_from_density = (
        int(raw.get("population_est", 0))
        if not isinstance(raw.get("population_est"), dict)
        else 0
    )
    population_estimate = max(pop_from_residential, pop_from_density)

    return {
        "population_estimate": population_estimate,
        "structures_total": int(ms.get("count", 0)) if isinstance(ms, dict) else 0,
        "residential_count": residential,
        "commercial_count": int(by_occ.get("Commercial", 0) or 0),
        "public_count": int(by_occ.get("Public", 0) or 0),
        "industrial_count": int(by_occ.get("Industrial", 0) or 0),
        "hospitals_count": len(hospitals),
        "hospitals_total_beds": sum(int(h.get("beds", 0) or 0) for h in hospitals),
        "schools_count": len(schools),
        "schools_total_enrollment": sum(int(s.get("enrollment", 0) or 0) for s in schools),
        "transmission_segments": len(lines),
        "transmission_max_kv": max(
            (float(ln.get("voltage_kv", 0) or 0) for ln in lines),
            default=0.0,
        ),
        "critical_facilities_total": (
            int(critical.get("total", 0)) if isinstance(critical, dict) else 0
        ),
    }


def _impact_findings(impact: dict) -> list[str]:
    """Headline bullets used in the cone label and key_findings list."""
    return [
        f"~{impact['population_estimate']:,} people in 24h cone",
        f"{impact['residential_count']:,} residential structures "
        f"({impact['structures_total']:,} total)",
        f"{impact['hospitals_count']} hospitals "
        f"({impact['hospitals_total_beds']:,} beds), "
        f"{impact['schools_count']} schools "
        f"({impact['schools_total_enrollment']:,} students)",
        f"{impact['transmission_segments']} transmission segments "
        f"(max {impact['transmission_max_kv']:.0f} kV), "
        f"{impact['critical_facilities_total']} critical facilities",
    ]


# --------------------------------------------------------------------------- #
# Main agent entry point
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict:  # noqa: C901
    tools = _spread_tools()
    np = _np()

    lat0 = state.incident.lat if state.incident else 34.7
    lon0 = state.incident.lon if state.incident else -119.8
    incident_name = state.incident.name if state.incident else "unknown"

    # ------------------------------------------------------------------ #
    # 1. Read prior subagent outputs
    # ------------------------------------------------------------------ #
    weather_out: AgentOutput | None = state.outputs.get("weather_wind")
    terrain_out: AgentOutput | None = state.outputs.get("terrain_fuel")

    missing = [n for n, o in [("weather_wind", weather_out), ("terrain_fuel", terrain_out)] if o is None]
    if missing:
        output = _low_confidence_output(
            incident_name,
            f"Required inputs not yet available: {', '.join(missing)}. "
            "Spread simulation deferred.",
        )
        return {"outputs": {AGENT_NAME: output}}

    weather = _extract_weather(weather_out)
    terrain = _extract_terrain_fuel(terrain_out)
    fm = _fuel_moisture(weather)

    # ------------------------------------------------------------------ #
    # 2. Build base inputs for Monte Carlo
    # ------------------------------------------------------------------ #
    # Weather reports wind in MET FROM convention; pyretechnics_spread expects
    # the fire-head TO direction (downwind heading). Flip by 180° here so the
    # ellipse rotates correctly, then preserve the FROM value in the output
    # payload for display.
    wind_from_deg = weather.get("wind_dir_deg", 270.0)
    wind_to_deg = (wind_from_deg + 180.0) % 360.0
    base_inputs = {
        "fuel_model": terrain.get("fuel_model", "GS2"),
        "slope_pct": terrain.get("slope_pct", 15.0),
        "aspect_deg": terrain.get("aspect_deg", 225.0),
        "wind_speed_mph": weather.get("wind_speed_mph", 15.0),
        "wind_dir_deg": wind_to_deg,
        "fuel_moisture": fm,
    }

    # ------------------------------------------------------------------ #
    # 3. Monte Carlo N=200
    # ------------------------------------------------------------------ #
    mc_result = tools["monte_carlo_cone"](base_inputs, n=200, hours=(1, 6, 12, 24))
    meta = mc_result.get("_meta", {})

    # ------------------------------------------------------------------ #
    # 4. Compute confidence
    # ------------------------------------------------------------------ #
    confidence, confidence_driver = _mc_confidence(mc_result)

    # ------------------------------------------------------------------ #
    # 5. GeoJSON cones for downstream agents
    # ------------------------------------------------------------------ #
    cones_geojson, cone_bands = _cones_to_geojson(
        mc_result, lat0, lon0, tools["polygon_local_to_geojson"]
    )

    high_risk_zones = _high_risk_zones(mc_result)

    # ------------------------------------------------------------------ #
    # 5b. Swept cone — fetch the WFIGS perimeter and Minkowski-sweep the
    #     24h cone over it. Both the visual and the impact queries use this
    #     polygon so the cone label exactly matches the painted region.
    # ------------------------------------------------------------------ #
    cone_impact: dict | None = None
    cone_24h_geojson = cones_geojson.get("24h")
    swept_cone_24h: dict | None = cone_24h_geojson

    if cone_24h_geojson is not None:
        try:
            from ..tools.wfigs_perimeter import (
                fetch_perimeter,
                perimeter_to_shapely,
            )

            irwin = _incident_irwin_id(state.incident)
            loop = asyncio.get_running_loop()
            perimeter_gj = await loop.run_in_executor(
                None,
                lambda: fetch_perimeter(irwin_id=irwin, lat=lat0, lon=lon0),
            )
            perimeter_geom = perimeter_to_shapely(perimeter_gj)
            swept_cone_24h = _build_swept_cone(
                cone_24h_geojson, perimeter_geom, state.incident
            )
        except Exception:  # noqa: BLE001 — sweep is best-effort
            swept_cone_24h = cone_24h_geojson

    impact_wkt = _cone_geojson_to_wkt(swept_cone_24h)
    if impact_wkt:
        bbox = _wkt_bbox(impact_wkt)
        if bbox is not None:
            try:
                cone_impact = await _gather_cone_impact(impact_wkt, bbox)
            except Exception as exc:  # noqa: BLE001
                cone_impact = {"error": f"impact_gather_failed:{exc}"}

    # 24-hour p25 burn area (km²) — key metric
    burn_area_km2 = None
    entry_24 = mc_result.get(24)
    if entry_24:
        bands = entry_24.get("bands", [])
        p25_poly = bands[0] if bands else None
        if p25_poly is not None and not p25_poly.is_empty:
            burn_area_km2 = round(p25_poly.area / 1_000_000.0, 2)

    # ------------------------------------------------------------------ #
    # 6. Trigger-point breach detection
    # ------------------------------------------------------------------ #
    raw_trigger_points = _trigger_points(state)
    trigger_breaches: list[dict] = []
    audit_records: list = []

    if raw_trigger_points:
        # Convert (lat, lon) pairs to local metres for spatial comparison.
        from shapely.geometry import Point

        local_tps = []
        for i, tp in enumerate(raw_trigger_points):
            if isinstance(tp, (list, tuple)) and len(tp) >= 2:
                tp_lat, tp_lon = float(tp[0]), float(tp[1])
                tp_id = tp[2] if len(tp) > 2 else f"TP-{i+1}"
            elif isinstance(tp, dict):
                tp_lat = float(tp.get("lat", tp.get("latitude", 0.0)))
                tp_lon = float(tp.get("lon", tp.get("longitude", 0.0)))
                tp_id = tp.get("id", f"TP-{i+1}")
            else:
                continue
            xm, ym = tools["lonlat_to_local"](tp_lon, tp_lat, lat0, lon0)
            local_tps.append((tp_id, Point(xm, ym)))

        trigger_breaches = tools["detect_trigger_breach"](mc_result, local_tps)

    # ------------------------------------------------------------------ #
    # 7. Trigger-point interrupt (HITL)
    # ------------------------------------------------------------------ #
    edited_cones: dict | None = None

    for breach in trigger_breaches:
        interrupt_payload = {
            "type": "trigger_point_breach",
            "trigger": breach["trigger_id"],
            "hours_until": breach["hours_until_breach"],
            "prob": breach["prob_at_breach"],
            "incident": incident_name,
            "cone_summary": {
                "head_ros_fpm": round(meta.get("ros_fpm_mean", 0.0), 1),
                "flame_length_ft": round(meta.get("flame_length_ft_mean", 0.0), 1),
                "24h_burn_area_km2": burn_area_km2,
            },
        }
        decision = request_human_decision("trigger_point_violation", interrupt_payload)
        audit_records.append(audit_entry("trigger_point_violation", interrupt_payload, decision))

        dec = decision.get("decision", "approve") if isinstance(decision, dict) else "approve"

        if dec == "reject":
            output = _low_confidence_output(
                incident_name,
                f"Spread run rejected by IC at trigger-point breach "
                f"({breach['trigger_id']}, {breach['hours_until_breach']}h). "
                "Awaiting updated inputs.",
            )
            return {
                "outputs": {AGENT_NAME: output},
                "audit_log": audit_records,
            }

        if dec == "edit":
            edited_cones = decision.get("edits", {}).get("cones") or None

        # "approve" → fall through and continue with existing cones.

    # Use edited cones if IC provided them.
    if edited_cones is not None:
        cones_geojson = edited_cones

    # ------------------------------------------------------------------ #
    # 8. LLM narrative
    # ------------------------------------------------------------------ #
    impact_str = ""
    if cone_impact and "error" not in cone_impact:
        impact_str = (
            f" | Cone impact: ~{cone_impact['population_estimate']:,} people, "
            f"{cone_impact['residential_count']:,} residences, "
            f"{cone_impact['hospitals_count']} hospitals, "
            f"{cone_impact['schools_count']} schools, "
            f"{cone_impact['critical_facilities_total']} critical facilities"
        )
    context_for_llm = (
        f"Incident: {incident_name} | Fuel: {base_inputs['fuel_model']} | "
        f"Slope: {base_inputs['slope_pct']}% | Wind: {base_inputs['wind_speed_mph']} mph "
        f"from {base_inputs['wind_dir_deg']}° | Dead 1h moisture: {fm['1h']}% | "
        f"Head ROS mean: {meta.get('ros_fpm_mean', 0.0):.1f} fpm ± "
        f"{meta.get('ros_fpm_std', 0.0):.1f} fpm | "
        f"Flame length mean: {meta.get('flame_length_ft_mean', 0.0):.1f} ft | "
        f"24h p25 burn area: {burn_area_km2} km² | "
        f"Trigger breaches: {trigger_breaches} | "
        f"Confidence: {confidence:.2f} ({confidence_driver})"
        f"{impact_str}"
    )
    llm_narrative = await _llm_narrate(context_for_llm)

    if not llm_narrative:
        ros_mph = meta.get("ros_fpm_mean", 0.0) * 60.0 / 5280.0
        ros_chains = meta.get("ros_fpm_mean", 0.0) * 60.0 / 66.0
        impact_sentence = ""
        if cone_impact and "error" not in cone_impact:
            impact_sentence = (
                f"Projected to expose ~{cone_impact['population_estimate']:,} people, "
                f"{cone_impact['residential_count']:,} residences, "
                f"{cone_impact['hospitals_count']} hospitals, "
                f"{cone_impact['schools_count']} schools, and "
                f"{cone_impact['critical_facilities_total']} critical facilities "
                "inside the 24h cone. "
            )
        llm_narrative = (
            f"SPREAD SIMULATION — {incident_name}: "
            f"Head ROS {ros_chains:.1f} chains/hr ({ros_mph:.1f} mph), "
            f"flame length {meta.get('flame_length_ft_mean', 0.0):.0f} ft. "
            f"24-hour projected burn area (≥25% probability): "
            f"{burn_area_km2 if burn_area_km2 is not None else 'N/A'} km². "
            f"{impact_sentence}"
            f"Confidence {confidence:.0%} — {confidence_driver}. "
            + (
                f"RECOMMEND immediate trigger-point review: "
                + ", ".join(
                    f"{b['trigger_id']} threatened at {b['hours_until_breach']}h "
                    f"(prob {b['prob_at_breach']:.0%})"
                    for b in trigger_breaches
                )
                if trigger_breaches
                else "No trigger-point breaches detected in modelled envelope."
            )
        )

    # ------------------------------------------------------------------ #
    # 9. Key findings
    # ------------------------------------------------------------------ #
    key_findings = [
        f"24h burn area (p25): {burn_area_km2} km²",
        f"Head ROS: {meta.get('ros_fpm_mean', 0.0):.1f} fpm "
        f"(±{meta.get('ros_fpm_std', 0.0):.1f} fpm, N=200)",
        f"Flame length: {meta.get('flame_length_ft_mean', 0.0):.0f} ft",
        f"Trigger-point breaches: "
        + (
            ", ".join(
                f"{b['trigger_id']} at {b['hours_until_breach']}h"
                for b in trigger_breaches
            )
            if trigger_breaches
            else "none"
        ),
    ]
    if cone_impact and "error" not in cone_impact:
        key_findings.extend(_impact_findings(cone_impact))

    # ------------------------------------------------------------------ #
    # 10. Citation bundle
    # ------------------------------------------------------------------ #
    pyro_ver = meta.get("pyretechnics_version", "unavailable")
    citations = CitationBundle(
        datasets=[
            Dataset(name="LANDFIRE FBFM40", version="2022", timestamp=None, url=None),
            Dataset(name="HRRR wind (via weather_wind subagent)"),
            Dataset(name="RAWS fuel moisture (via weather_wind subagent)"),
        ],
        models=[
            Model(name="Rothermel surface-fire ROS", version="1972/Albini-1976"),
            Model(
                name=f"pyretechnics surface_fire ({pyro_ver})",
                version=pyro_ver,
            ),
            Model(name="Anderson elliptical cone", version="1983"),
            Model(name=f"Monte Carlo N={meta.get('n', 200)}"),
        ],
        reasoning_trace_id=str(uuid.uuid4()),
    )

    # ------------------------------------------------------------------ #
    # 11. Build output
    # ------------------------------------------------------------------ #
    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=llm_narrative,
        payload={
            "key_findings": key_findings,
            "cones": cones_geojson,
            "cone_bands": cone_bands,
            "head_ros_chains_per_hr": round(meta.get("ros_fpm_mean", 0.0) * 60.0 / 66.0, 2),
            "head_ros_fpm_mean": round(meta.get("ros_fpm_mean", 0.0), 1),
            "head_ros_fpm_std": round(meta.get("ros_fpm_std", 0.0), 1),
            "flame_length_ft": round(meta.get("flame_length_ft_mean", 0.0), 1),
            "burn_area_24h_km2_p25": burn_area_km2,
            "trigger_breaches": trigger_breaches,
            "high_risk_zones": high_risk_zones,
            "cone_impact": cone_impact,
            "swept_cone_24h": swept_cone_24h,
            "n_mc_samples": int(meta.get("n", 200)),
            "fuel_model": base_inputs["fuel_model"],
            "wind_speed_mph": base_inputs["wind_speed_mph"],
            # Surface MET FROM convention in the payload (display contract);
            # the +180° fire-head TO direction lives inside base_inputs only.
            "wind_dir_deg": wind_from_deg,
            "fuel_moisture": fm,
        },
        confidence=confidence,
        confidence_driver=confidence_driver,
        citation_bundle=citations,
    )

    patch: dict = {"outputs": {AGENT_NAME: output}}
    if audit_records:
        patch["audit_log"] = audit_records
    return patch


# --------------------------------------------------------------------------- #
# Low-confidence early-exit helper
# --------------------------------------------------------------------------- #


def _low_confidence_output(incident_name: str, reason: str) -> AgentOutput:
    return AgentOutput(
        agent=AGENT_NAME,
        narrative=f"[{AGENT_NAME}] {reason}",
        payload={
            "key_findings": [reason],
            "cones": {"1h": None, "6h": None, "12h": None, "24h": None},
            "cone_bands": {
                "1h": {"p25": None, "p50": None, "p75": None, "p95": None},
                "6h": {"p25": None, "p50": None, "p75": None, "p95": None},
                "12h": {"p25": None, "p50": None, "p75": None, "p95": None},
                "24h": {"p25": None, "p50": None, "p75": None, "p95": None},
            },
            "head_ros_chains_per_hr": None,
            "flame_length_ft": None,
            "trigger_breaches": [],
            "high_risk_zones": [],
            "cone_impact": None,
            "swept_cone_24h": None,
        },
        confidence=0.20,
        confidence_driver="insufficient upstream inputs",
        citation_bundle=CitationBundle(
            datasets=[Dataset(name="(no data)")],
            models=[Model(name="(no model run)")],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )


# --------------------------------------------------------------------------- #
# Smoke test — Los Padres NF fixture
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import asyncio
    import json

    # ------------------------------------------------------------------ #
    # Mock state with pre-populated weather + terrain outputs
    # ------------------------------------------------------------------ #

    from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Incident, Model

    _INCIDENT = Incident(
        id="CAL-LPF-2026-001",
        name="Los Padres Test Fire",
        lat=34.72,
        lon=-119.85,
        acres=150.0,
        source="synthetic",
        raw={
            "trigger_points": [
                # Sizable ranch to NNW — expected breach by 6h under NW winds
                {"id": "Figueroa-Ranch", "lat": 34.77, "lon": -119.90},
                # Hwy 154 / Cold Springs saddle to the south
                {"id": "Hwy154-Saddle", "lat": 34.69, "lon": -119.83},
            ]
        },
    )

    _WEATHER_OUT = AgentOutput(
        agent="weather_wind",
        narrative="Santa Ana onset. NW 22 mph, RH 9%, temp 95°F.",
        payload={
            "wind_speed_mph": 22.0,
            "wind_dir_deg": 315.0,  # fire head goes to 135° (SE)
            "rh_pct": 9.0,
            "temp_f": 95.0,
            "fuel_moisture": {
                "1h": 4.0,
                "10h": 7.0,
                "100h": 9.0,
                "live_herb": 55.0,
                "live_woody": 85.0,
            },
        },
        confidence=0.80,
    )

    _TERRAIN_OUT = AgentOutput(
        agent="terrain_fuel",
        narrative="SH5 chamise chaparral, 28% slope, S-facing.",
        payload={
            "fuel_model": "SH5",
            "slope_pct": 28.0,
            "aspect_deg": 180.0,
        },
        confidence=0.75,
    )

    _state = AgentState(
        incident=_INCIDENT,
        operational_period=1,
        user_query="Project fire spread over next 24 hours.",
        outputs={
            "weather_wind": _WEATHER_OUT,
            "terrain_fuel": _TERRAIN_OUT,
        },
    )

    # ------------------------------------------------------------------ #
    # Run without HITL (no trigger points in this first pass)
    # ------------------------------------------------------------------ #

    async def _smoke_no_triggers():
        state_no_tp = _state.model_copy(
            update={"incident": _INCIDENT.model_copy(update={"raw": {}})}
        )
        print("=== SMOKE TEST 1: no trigger points ===")
        result = await run(state_no_tp)
        out: AgentOutput = result["outputs"][AGENT_NAME]
        print(f"narrative    : {out.narrative[:200]}")
        print(f"confidence   : {out.confidence:.2f} — {out.confidence_driver}")
        kf = out.payload.get("key_findings", [])
        for f in kf:
            print(f"  finding    : {f}")
        print(f"head ROS     : {out.payload.get('head_ros_chains_per_hr')} chains/hr")
        print(f"flame length : {out.payload.get('flame_length_ft')} ft")
        cones = out.payload.get("cones", {})
        bands = out.payload.get("cone_bands", {})
        for h in (1, 6, 12, 24):
            key = f"{h}h"
            cone_p50 = cones.get(key)
            band_p25 = (bands.get(key) or {}).get("p25")
            has_p50 = cone_p50 is not None
            has_p25 = band_p25 is not None
            print(
                f"  cone {h:2d}h   : "
                f"{'p50 present' if has_p50 else 'MISSING p50'}, "
                f"{'p25 band present' if has_p25 else 'MISSING p25 band'}"
            )
        print()

    # ------------------------------------------------------------------ #
    # Smoke test 2: trigger breaches caught by interrupt (simulate approval)
    # ------------------------------------------------------------------ #

    async def _smoke_with_triggers():
        print("=== SMOKE TEST 2: trigger points (simulate interrupt → approve) ===")
        try:
            result = await run(_state)
            # If we reach here, no interrupt was fired (no actual breach)
            out: AgentOutput = result["outputs"][AGENT_NAME]
            breaches = out.payload.get("trigger_breaches", [])
            print(f"trigger breaches (no interrupt): {breaches}")
        except Exception as exc:
            # LangGraph's interrupt() raises GraphInterrupt outside the graph.
            exc_name = type(exc).__name__
            print(f"interrupt raised (expected outside graph): {exc_name}")
            print(f"  message: {str(exc)[:200]}")
        print()

    async def _main():
        await _smoke_no_triggers()
        await _smoke_with_triggers()
        print("smoke test complete.")

    asyncio.run(_main())
