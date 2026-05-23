"""Weather & Wind subagent (FBAN-leaning).

Fuses four data streams into a 24-hour fire-weather picture for the incident
AOI:

* HRRR forecast hours 0..24 via Herbie (gridded model)
* RTMA analysis via Herbie (nowcast / reanalysis check)
* RAWS observations within ~50 km via SynopticPy
* NWS active alerts + hourly forecast for the incident point

The LLM (Claude Haiku 4.5) writes a short FBAN-style narrative over the fused
data. We always compute a deterministic Red Flag flag and confidence score so
the rest of the graph has a usable answer even when the LLM is offline.

Module contract (enforced repo-wide):

    AGENT_NAME == "weather_wind"
    async def run(state) -> {"outputs": {AGENT_NAME: AgentOutput}}
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from typing import Any

from ..state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Dataset,
    Model,
)

AGENT_NAME = "weather_wind"

_PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "prompts" / "fban.md"
)
_LLM_MODEL = os.environ.get(
    "EMBERSIGHT_MODEL_WEATHER_WIND", "claude-haiku-4-5"
)

# --------------------------------------------------------------------------- #
# Fusion helpers
# --------------------------------------------------------------------------- #


def _circular_diff_deg(a: float, b: float) -> float:
    """Smallest signed angular distance between two compass bearings (deg)."""
    d = (a - b + 540.0) % 360.0 - 180.0
    return abs(d)


def _model_agreement_confidence(
    hrrr_hourly: list[dict[str, Any]], rtma: dict[str, Any]
) -> tuple[float, str]:
    """Confidence ∝ wind-direction agreement between HRRR(f=0) and RTMA.

    Returns (confidence in [0,1], driver string).
    """
    rtma_dir = rtma.get("wind_direction_deg")
    if not hrrr_hourly or rtma_dir is None:
        return 0.35, "HRRR or RTMA unavailable; degraded confidence"

    hrrr0 = next(
        (h for h in hrrr_hourly if h.get("fxx") == 0 and h.get("wind_direction_deg") is not None),
        None,
    )
    if hrrr0 is None:
        return 0.4, "HRRR f=0 wind direction missing"

    diff = _circular_diff_deg(hrrr0["wind_direction_deg"], rtma_dir)
    if diff <= 15.0:
        confidence = 1.0
    elif diff >= 90.0:
        confidence = 0.2
    else:
        confidence = max(0.2, 1.0 - (diff - 15.0) / 75.0 * 0.8)
    return (
        round(confidence, 2),
        f"HRRR vs RTMA wind direction differ by {diff:.0f}° at f=0",
    )


def _red_flag_from_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for f in alerts:
        props = f.get("properties", {})
        event = (props.get("event") or "").lower()
        if "red flag" in event or "fire weather watch" in event:
            return {
                "event": props.get("event"),
                "headline": props.get("headline"),
                "severity": props.get("severity"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
                "url": (f.get("id") or props.get("@id")),
            }
    return None


def _derived_red_flag(
    hrrr_hourly: list[dict[str, Any]],
    raws: dict[str, Any],
) -> dict[str, Any] | None:
    """Climatological Red Flag heuristic: RH<25, wind>25 mph, temp>75 F.

    Checks the HRRR forecast (any hour in the window) first, then falls back
    to the most recent RAWS obs if HRRR is unavailable.
    """
    for hour in hrrr_hourly:
        rh = hour.get("rh_pct")
        wind = hour.get("wind_speed_mph")
        temp = hour.get("temp_f")
        if rh is not None and wind is not None and temp is not None:
            if rh < 25.0 and wind > 25.0 and temp > 75.0:
                return {
                    "source": "derived_hrrr",
                    "valid_time": hour.get("valid_time"),
                    "rh_pct": rh,
                    "wind_speed_mph": wind,
                    "temp_f": temp,
                }

    for st in raws.get("stations", []):
        latest = st.get("latest", {})
        rh = (latest.get("relative_humidity") or {}).get("value")
        wind = (latest.get("wind_speed") or {}).get("value")
        temp = (latest.get("air_temp") or {}).get("value")
        if rh is not None and wind is not None and temp is not None:
            if rh < 25.0 and wind > 25.0 and temp > 75.0:
                return {
                    "source": "derived_raws",
                    "stid": st.get("stid"),
                    "rh_pct": rh,
                    "wind_speed_mph": wind,
                    "temp_f": temp,
                }
    return None


def _critical_window(hrrr_hourly: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the worst hour by wind^2 * (100-RH). Cheap proxy for fire weather load."""
    best: tuple[float, dict[str, Any]] | None = None
    for h in hrrr_hourly:
        wind = h.get("wind_speed_mph")
        rh = h.get("rh_pct")
        if wind is None or rh is None:
            continue
        score = (wind ** 2) * max(0.0, 100.0 - rh)
        if best is None or score > best[0]:
            best = (score, h)
    if best is None:
        return None
    score, h = best
    return {
        "valid_time": h.get("valid_time"),
        "wind_speed_mph": h.get("wind_speed_mph"),
        "wind_direction_deg": h.get("wind_direction_deg"),
        "rh_pct": h.get("rh_pct"),
        "temp_f": h.get("temp_f"),
        "score": round(score, 1),
    }


# --------------------------------------------------------------------------- #
# LLM narrative (optional)
# --------------------------------------------------------------------------- #


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text()
    except OSError:
        return "You are a Fire Behavior Analyst (FBAN). Summarize fire weather."


async def _llm_narrative(fused: dict[str, Any]) -> str | None:
    """Run Claude Haiku 4.5 over the fused data. Returns None when no key."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage
    except Exception:  # noqa: BLE001
        return None

    llm = ChatAnthropic(model=_LLM_MODEL, max_tokens=600, timeout=30)
    system = _load_prompt()
    human = (
        "Fused fire-weather inputs for the incident AOI follow as JSON. "
        "Write a 4-6 sentence FBAN-style summary covering: 24-hour wind "
        "trend, RH crossover risk, any Red Flag / Fire Weather Watch in "
        "effect, the worst-case hour, and HRRR/RTMA agreement. Use the "
        "RECOMMEND / PROPOSE / SUGGEST verbs only.\n\n"
        f"{json.dumps(fused, default=str)[:8000]}"
    )
    try:
        resp = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
        return str(resp.content).strip()
    except Exception:  # noqa: BLE001
        return None


def _deterministic_narrative(fused: dict[str, Any]) -> str:
    """Fallback narrative used when the LLM is unavailable."""
    inc = fused.get("incident_name", "the incident")
    rf = fused.get("red_flag")
    crit = fused.get("critical_window")
    conf = fused.get("confidence", 0.5)
    parts = [
        f"FBAN brief for {inc}.",
    ]
    if rf and rf.get("source", "").startswith("nws"):
        parts.append(f"RECOMMEND treating NWS {rf['event']} as actionable.")
    elif rf:
        parts.append(
            "RECOMMEND elevated fire-weather posture: derived Red Flag conditions "
            f"({rf.get('rh_pct')}% RH, {rf.get('wind_speed_mph')} mph, "
            f"{rf.get('temp_f')}°F)."
        )
    else:
        parts.append("No Red Flag Warning in effect.")
    if crit:
        parts.append(
            f"Worst HRRR hour {crit.get('valid_time')}: "
            f"{crit.get('wind_speed_mph')} mph from {crit.get('wind_direction_deg')}°, "
            f"RH {crit.get('rh_pct')}%, {crit.get('temp_f')}°F."
        )
    parts.append(f"PROPOSE confidence {conf:.2f} (HRRR/RTMA agreement).")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Flat-key projection for downstream consumers
# --------------------------------------------------------------------------- #


def _flat_wx_keys(
    rtma: dict[str, Any],
    hrrr_hourly: list[dict[str, Any]],
    critical: dict[str, Any] | None,
    raws: dict[str, Any],
) -> dict[str, Any]:
    """Project the richest source into the flat keys spread_simulation expects.

    Source preference per key: RTMA now → HRRR f=0 → critical window → latest RAWS.
    Defaults match spread_simulation's hardcoded fallbacks (15 mph @ 270°,
    20% RH, 90°F) so the degraded path stays deterministic.
    """

    def _first_non_none(*candidates: Any) -> Any:
        for c in candidates:
            if c is not None:
                return c
        return None

    hrrr0 = next(
        (h for h in hrrr_hourly if h.get("fxx") == 0),
        None,
    ) or {}
    crit = critical or {}
    raws_latest: dict[str, Any] = {}
    for st in raws.get("stations", []):
        latest = st.get("latest") or {}
        raws_latest = {
            "wind_speed_mph": (latest.get("wind_speed") or {}).get("value"),
            "wind_dir_deg": (latest.get("wind_direction") or {}).get("value"),
            "temp_f": (latest.get("air_temp") or {}).get("value"),
            "rh_pct": (latest.get("relative_humidity") or {}).get("value"),
        }
        break

    wind_speed = _first_non_none(
        rtma.get("wind_speed_mph"),
        hrrr0.get("wind_speed_mph"),
        crit.get("wind_speed_mph"),
        raws_latest.get("wind_speed_mph"),
        15.0,
    )
    wind_dir = _first_non_none(
        rtma.get("wind_direction_deg"),
        hrrr0.get("wind_direction_deg"),
        crit.get("wind_direction_deg"),
        raws_latest.get("wind_dir_deg"),
        270.0,
    )
    temp_f = _first_non_none(
        rtma.get("temp_f"),
        hrrr0.get("temp_f"),
        crit.get("temp_f"),
        raws_latest.get("temp_f"),
        90.0,
    )
    rh = _first_non_none(
        rtma.get("rh_pct"),
        hrrr0.get("rh_pct"),
        crit.get("rh_pct"),
        raws_latest.get("rh_pct"),
        20.0,
    )
    return {
        "wind_speed_mph": float(wind_speed),
        "wind_dir_deg": float(wind_dir),
        "temp_f": float(temp_f),
        "rh_pct": float(rh),
        "fuel_moisture": {},  # weather_wind has no fuel-moisture pipeline yet
    }


# --------------------------------------------------------------------------- #
# Concurrent fetch
# --------------------------------------------------------------------------- #


async def _gather_inputs(lat: float, lon: float) -> dict[str, Any]:
    """Pull NWS + RAWS + HRRR + RTMA in parallel.

    Imports are deferred so a graph-import smoke test doesn't require the
    science group to be installed.
    """
    from ..tools import herbie_wx, nws, synoptic_raws

    bbox = synoptic_raws.bbox_around(lat, lon, 50.0)

    forecast_task = nws.get_forecast(lat, lon)
    alerts_task = nws.get_active_alerts(lat, lon)
    raws_task = synoptic_raws.get_raws_observations(bbox, lookback_hours=24)
    hrrr_task = asyncio.to_thread(herbie_wx.get_hrrr_forecast, lat, lon, 24)
    rtma_task = asyncio.to_thread(herbie_wx.get_rtma_analysis, lat, lon)

    forecast, alerts, raws, hrrr, rtma = await asyncio.gather(
        forecast_task,
        alerts_task,
        raws_task,
        hrrr_task,
        rtma_task,
        return_exceptions=True,
    )

    def _coerce(obj: Any, fallback: Any) -> Any:
        if isinstance(obj, Exception):
            return {"error": f"{type(obj).__name__}: {obj}", **fallback}
        return obj

    return {
        "nws_forecast": _coerce(forecast, {"forecast": None}),
        "nws_alerts": (
            [] if isinstance(alerts, Exception) else alerts
        ),
        "raws": _coerce(raws, {"station_count": 0, "stations": []}),
        "hrrr": _coerce(hrrr, {"hourly": []}),
        "rtma": _coerce(rtma, {}),
        "bbox": list(bbox),
    }


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict:
    incident = state.incident
    if incident is None:
        return {
            "outputs": {
                AGENT_NAME: AgentOutput(
                    agent=AGENT_NAME,
                    narrative="No incident on state; weather_wind has nothing to analyze.",
                    payload={"reason": "no_incident"},
                    confidence=0.0,
                    confidence_driver="missing incident",
                    citation_bundle=CitationBundle(
                        reasoning_trace_id=str(uuid.uuid4()),
                    ),
                )
            }
        }

    lat, lon = incident.lat, incident.lon
    inputs = await _gather_inputs(lat, lon)

    hrrr_hourly = inputs["hrrr"].get("hourly", []) if isinstance(inputs["hrrr"], dict) else []
    rtma = inputs["rtma"] if isinstance(inputs["rtma"], dict) else {}
    raws = inputs["raws"] if isinstance(inputs["raws"], dict) else {"stations": []}

    confidence, driver = _model_agreement_confidence(hrrr_hourly, rtma)
    red_flag_nws = _red_flag_from_alerts(inputs["nws_alerts"])
    red_flag = (
        {**red_flag_nws, "source": "nws_alert"}
        if red_flag_nws
        else _derived_red_flag(hrrr_hourly, raws)
    )
    critical = _critical_window(hrrr_hourly)

    fused = {
        "incident_name": incident.name,
        "lat": lat,
        "lon": lon,
        "operational_period": state.operational_period,
        "red_flag": red_flag,
        "critical_window": critical,
        "confidence": confidence,
        "confidence_driver": driver,
        "hrrr_first_3h": hrrr_hourly[:3],
        "hrrr_last_3h": hrrr_hourly[-3:],
        "rtma_now": {
            k: rtma.get(k)
            for k in ("valid_time", "wind_speed_mph", "wind_direction_deg", "temp_f", "rh_pct")
        },
        "raws_stations": [
            {"stid": s.get("stid"), "name": s.get("name"), "latest": s.get("latest")}
            for s in raws.get("stations", [])[:8]
        ],
        "active_nws_alerts": [
            (f.get("properties") or {}).get("event") for f in inputs["nws_alerts"]
        ],
    }

    narrative = await _llm_narrative(fused) or _deterministic_narrative(fused)

    citations = CitationBundle(
        datasets=[
            Dataset(
                name="NWS Active Alerts",
                url=f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}",
            ),
            Dataset(
                name="NWS Hourly Forecast",
                url=(inputs["nws_forecast"] or {}).get("forecast_url"),
            ),
            Dataset(
                name="HRRR (Herbie)",
                version=inputs["hrrr"].get("run") if isinstance(inputs["hrrr"], dict) else None,
            ),
            Dataset(
                name="RTMA (Herbie)",
                version=rtma.get("run"),
            ),
            Dataset(
                name="Synoptic RAWS",
                version=",".join(
                    str(s.get("stid")) for s in raws.get("stations", [])[:10]
                )
                or None,
            ),
        ],
        models=[
            Model(name="claude-haiku-4-5", version="anthropic"),
        ],
        reasoning_trace_id=str(uuid.uuid4()),
    )

    # Flat top-level keys for downstream consumers (spread_simulation).
    # Source preference: RTMA now → HRRR f=0 → critical window → first RAWS.
    # Defaults match spread_simulation's `_extract_weather` fallbacks so a
    # silent-degrade path remains deterministic.
    flat = _flat_wx_keys(rtma, hrrr_hourly, critical, raws)

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload={
            # Flat keys: contract with spread_simulation._extract_weather.
            "wind_speed_mph": flat["wind_speed_mph"],
            "wind_dir_deg": flat["wind_dir_deg"],
            "temp_f": flat["temp_f"],
            "rh_pct": flat["rh_pct"],
            "fuel_moisture": flat["fuel_moisture"],
            # Detailed structures for the UI / debugging.
            "red_flag": red_flag,
            "critical_window": critical,
            "hrrr_hourly": hrrr_hourly,
            "rtma_now": fused["rtma_now"],
            "raws_summary": {
                "station_count": raws.get("station_count", 0),
                "stations": fused["raws_stations"],
                "error": raws.get("error"),
            },
            "nws_alerts": [
                {
                    "event": (f.get("properties") or {}).get("event"),
                    "severity": (f.get("properties") or {}).get("severity"),
                    "headline": (f.get("properties") or {}).get("headline"),
                    "url": f.get("id"),
                }
                for f in inputs["nws_alerts"]
            ],
            "bbox": inputs["bbox"],
        },
        confidence=confidence,
        confidence_driver=driver,
        citation_bundle=citations,
    )

    return {"outputs": {AGENT_NAME: output}}


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    from ..state import Incident
    from ..tools.seed_demo import make_synthetic_incident

    async def _smoke() -> None:
        raw = make_synthetic_incident()
        state = AgentState(
            incident=Incident(**raw),
            operational_period=1,
            user_query="24h fire weather for Demo Ridge Fire",
        )
        patch = await run(state)
        out = patch["outputs"][AGENT_NAME]
        print("=" * 60)
        print(f"agent: {out.agent}")
        print(f"confidence: {out.confidence}  ({out.confidence_driver})")
        print(f"narrative: {out.narrative}")
        print("payload keys:", sorted(out.payload.keys()))
        red = out.payload.get("red_flag")
        print(f"red_flag: {red}")
        critical = out.payload.get("critical_window")
        print(f"critical_window: {critical}")
        hourly = out.payload.get("hrrr_hourly") or []
        print(f"hrrr hours: {len(hourly)}")
        raws = out.payload.get("raws_summary") or {}
        print(
            f"raws stations: {raws.get('station_count')} "
            f"(error: {raws.get('error', 'none')})"
        )
        alerts = out.payload.get("nws_alerts") or []
        print(f"nws alerts: {len(alerts)}")
        print(f"citation datasets: {len(out.citation_bundle.datasets)}")

    asyncio.run(_smoke())
