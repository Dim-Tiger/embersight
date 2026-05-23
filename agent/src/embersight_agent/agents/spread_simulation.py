"""Spread Simulation subagent — Pass-2.

Pyretechnics surface-fire ROS + Anderson elliptical cone + Monte Carlo N=200
perturbing wind speed/direction and fuel moisture.

Outputs 1h / 6h / 12h / 24h probability-of-burn polygons and interrupts on
trigger-point violations.
"""

from __future__ import annotations

import math
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

from ..hitl import audit_entry, request_human_decision
from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model

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

        llm = ChatAnthropic(model=model_name, max_tokens=800, temperature=0.2)
        msgs = [
            SystemMessage(content=prompt_text or "You are a wildland fire analyst."),
            HumanMessage(content=context),
        ]
        resp = await llm.ainvoke(msgs)
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Input extraction helpers
# --------------------------------------------------------------------------- #


def _extract_weather(weather_output: AgentOutput | None) -> dict:
    """Pull wind speed, wind direction, and RAWS fuel moisture from weather payload."""
    if weather_output is None:
        return {}
    p = weather_output.payload or {}
    return {
        "wind_speed_mph": float(p.get("wind_speed_mph", 15.0)),
        "wind_dir_deg": float(p.get("wind_dir_deg", 270.0)),  # TO direction (downwind)
        "fuel_moisture": p.get("fuel_moisture", {}),
        "rh_pct": float(p.get("rh_pct", 20.0)),
        "temp_f": float(p.get("temp_f", 90.0)),
    }


def _extract_terrain_fuel(terrain_output: AgentOutput | None) -> dict:
    """Pull fuel model, slope, and aspect from terrain payload."""
    if terrain_output is None:
        return {}
    p = terrain_output.payload or {}
    return {
        "fuel_model": str(p.get("fuel_model", "GS2")),
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
) -> dict:
    """Convert monte_carlo_cone output to {hour: [gj_p25, gj_p50, gj_p75, gj_p95]}."""
    out: dict = {}
    for h in (1, 6, 12, 24):
        if h not in mc_result:
            out[h] = [None, None, None, None]
            continue
        bands = mc_result[h].get("bands", [None, None, None, None])
        out[h] = [polygon_local_to_geojson_fn(b, lat0, lon0) for b in bands]
    return out


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
    base_inputs = {
        "fuel_model": terrain.get("fuel_model", "GS2"),
        "slope_pct": terrain.get("slope_pct", 15.0),
        "aspect_deg": terrain.get("aspect_deg", 225.0),
        "wind_speed_mph": weather.get("wind_speed_mph", 15.0),
        "wind_dir_deg": weather.get("wind_dir_deg", 270.0),
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
    cones_geojson = _cones_to_geojson(
        mc_result, lat0, lon0, tools["polygon_local_to_geojson"]
    )

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
    )
    llm_narrative = await _llm_narrate(context_for_llm)

    if not llm_narrative:
        ros_mph = meta.get("ros_fpm_mean", 0.0) * 60.0 / 5280.0
        ros_chains = meta.get("ros_fpm_mean", 0.0) * 60.0 / 66.0
        llm_narrative = (
            f"SPREAD SIMULATION — {incident_name}: "
            f"Head ROS {ros_chains:.1f} chains/hr ({ros_mph:.1f} mph), "
            f"flame length {meta.get('flame_length_ft_mean', 0.0):.0f} ft. "
            f"24-hour projected burn area (≥25% probability): "
            f"{burn_area_km2 if burn_area_km2 is not None else 'N/A'} km². "
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
            "head_ros_chains_per_hr": round(meta.get("ros_fpm_mean", 0.0) * 60.0 / 66.0, 2),
            "head_ros_fpm_mean": round(meta.get("ros_fpm_mean", 0.0), 1),
            "head_ros_fpm_std": round(meta.get("ros_fpm_std", 0.0), 1),
            "flame_length_ft": round(meta.get("flame_length_ft_mean", 0.0), 1),
            "burn_area_24h_km2_p25": burn_area_km2,
            "trigger_breaches": trigger_breaches,
            "n_mc_samples": int(meta.get("n", 200)),
            "fuel_model": base_inputs["fuel_model"],
            "wind_speed_mph": base_inputs["wind_speed_mph"],
            "wind_dir_deg": base_inputs["wind_dir_deg"],
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
            "cones": {"1": None, "6": None, "12": None, "24": None},
            "head_ros_chains_per_hr": None,
            "flame_length_ft": None,
            "trigger_breaches": [],
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
        for h in (1, 6, 12, 24):
            bands = cones.get(h, [None, None, None, None])
            has_p25 = bands[0] is not None if bands else False
            print(f"  cone {h:2d}h   : {'p25 present' if has_p25 else 'MISSING'}")
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
