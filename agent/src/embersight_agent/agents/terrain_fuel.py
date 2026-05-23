"""Terrain & Fuel subagent.

Pulls LANDFIRE FBFM40 + USGS DEM (slope/aspect/elevation) + LANDFIRE canopy
cover for the incident AOI (~50 km bbox around the ignition point), fuses
the rasters, and asks Claude Haiku 4.5 for a short IMT-facing narrative.

Confidence = fuel-model purity (1 - normalized Shannon entropy of the
FBFM40 class distribution). High purity = one fuel model dominates the
AOI; low purity = patchy mosaic and the simulation downstream should
weight that uncertainty.

No `interrupt()` here — terrain is reference data, not actionable.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from ..state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Dataset,
    Model,
)

AGENT_NAME = "terrain_fuel"
LLM_MODEL = "claude-haiku-4-5"
AOI_RADIUS_KM = 50.0

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "terrain_fuel.md"


def _system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text()
    except FileNotFoundError:
        return "Characterize the fuel and topography in the incident AOI."


def _confidence_driver(purity: float, n_classes: int) -> str:
    if purity >= 0.75:
        return f"low entropy: {n_classes} FBFM40 classes, one strongly dominates"
    if purity >= 0.45:
        return f"moderate entropy across {n_classes} FBFM40 classes — mixed but ranked"
    return f"high entropy across {n_classes} FBFM40 classes — patchy fuel mosaic"


def _key_findings(fuel: dict, terrain: dict, canopy: dict) -> list[str]:
    findings: list[str] = []
    if fuel.get("dominant_classes"):
        top = fuel["dominant_classes"][0]
        findings.append(
            f"Dominant fuel: {top['code']} ({top['fraction']*100:.1f}% of AOI)"
        )
    slope = terrain.get("slope_deg", {})
    aspect_dist = terrain.get("aspect_distribution", {})
    if slope and slope.get("pixels"):
        top_aspect = (
            max(aspect_dist.items(), key=lambda kv: kv[1])
            if aspect_dist
            else ("n/a", 0.0)
        )
        findings.append(
            f"Slope p90 {slope['p90']:.0f}° (mean {slope['mean']:.0f}°); "
            f"dominant aspect {top_aspect[0]} ({top_aspect[1]*100:.0f}%)"
        )
    if canopy.get("pixels"):
        findings.append(
            f"Canopy cover mean {canopy['mean_pct']:.0f}% "
            f"(p10 {canopy['p10_pct']:.0f}%, p90 {canopy['p90_pct']:.0f}%)"
        )
    return findings


def _stub_narrative(incident_name: str, fuel: dict, terrain: dict, canopy: dict) -> str:
    parts = [f"[stub] Terrain & fuel summary for {incident_name}."]
    if fuel.get("dominant_classes"):
        top = fuel["dominant_classes"][0]
        parts.append(
            f"Dominant FBFM40 class is {top['code']} "
            f"at {top['fraction']*100:.0f}% of AOI; purity={fuel['purity']:.2f}."
        )
    slope = terrain.get("slope_deg", {})
    if slope.get("pixels"):
        parts.append(f"Slope mean {slope['mean']:.0f}°, p90 {slope['p90']:.0f}°.")
    if canopy.get("pixels"):
        parts.append(f"Canopy cover mean {canopy['mean_pct']:.0f}%.")
    parts.append("RECOMMEND verifying fuel model in field if purity < 0.5.")
    return " ".join(parts)


async def _haiku_narrative(
    incident: dict, fuel: dict, terrain: dict, canopy: dict
) -> str | None:
    """Call Claude Haiku 4.5 with the fused payload. Returns None if no API key
    or the SDK call fails — caller falls back to the deterministic stub."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import AsyncAnthropic  # lazy
    except ImportError:
        return None

    user_payload = {
        "incident": incident,
        "fuel_model": fuel,
        "terrain": terrain,
        "canopy": canopy,
    }
    user_msg = (
        "Fused LANDFIRE + DEM data for the incident AOI is below. "
        "Produce a 4-6 sentence narrative for an IMT planning section: "
        "dominant fuel model and behavior implications, slope/aspect drivers, "
        "canopy structure, and one RECOMMEND/PROPOSE/SUGGEST line on "
        "where the data is weakest. Keep numbers concrete.\n\n"
        f"DATA:\n{json.dumps(user_payload, default=str)[:12000]}"
    )

    try:
        client = AsyncAnthropic()
        resp = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=600,
            system=_system_prompt(),
            messages=[{"role": "user", "content": user_msg}],
        )
        chunks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "\n".join(chunks).strip() or None
    except Exception:
        return None


def _build_citations(
    fuel: dict, terrain: dict, canopy: dict, model_called: bool
) -> CitationBundle:
    datasets = [
        Dataset(
            name=f"LANDFIRE FBFM40 ({fuel.get('layer', 'unknown')})",
            version="LF 2020 (2022)",
            url="https://landfire.gov/fuel/fbfm40",
        ),
        Dataset(
            name=f"LANDFIRE Slope/Aspect/Elevation ({terrain.get('layers', {})})",
            version="LF 2020",
            url="https://landfire.gov/topographic",
        ),
        Dataset(
            name=f"LANDFIRE Canopy Cover ({canopy.get('layer', 'unknown')})",
            version="LF 2020 (2022)",
            url="https://landfire.gov/vegetation/cc",
        ),
    ]
    models = [
        Model(
            name=LLM_MODEL if model_called else f"{LLM_MODEL} (skipped: no key)",
            version="2026-01",
        )
    ]
    return CitationBundle(
        datasets=datasets,
        models=models,
        reasoning_trace_id=str(uuid.uuid4()),
    )


async def _gather_geo(
    bbox: tuple[float, float, float, float]
) -> tuple[dict, dict, dict]:
    """Fan the three blocking LANDFIRE pulls off the event loop."""
    from ..tools import landfire as lf  # lazy

    return await asyncio.gather(
        asyncio.to_thread(lf.get_fuel_model, bbox),
        asyncio.to_thread(lf.get_terrain, bbox),
        asyncio.to_thread(lf.get_canopy_cover, bbox),
    )


def _empty_geo_payload(error: str) -> tuple[dict, dict, dict]:
    placeholder = {"error": error, "pixels": 0}
    return (
        {**placeholder, "class_distribution": {}, "dominant_classes": [], "purity": 0.0},
        {**placeholder, "slope_deg": {}, "aspect_distribution": {}, "elevation_m": {}},
        {**placeholder, "distribution": {}},
    )


async def run(state: AgentState) -> dict[str, Any]:
    from ..tools.landfire import bbox_around  # lazy, keeps imports light

    if state.incident is None:
        return {
            "outputs": {
                AGENT_NAME: AgentOutput(
                    agent=AGENT_NAME,
                    narrative="No incident on state; skipping terrain/fuel pull.",
                    payload={"skipped": True},
                    confidence=0.0,
                    confidence_driver="no incident",
                    citation_bundle=CitationBundle(),
                )
            }
        }

    incident = state.incident
    bbox = bbox_around(incident.lat, incident.lon, km=AOI_RADIUS_KM)

    try:
        fuel, terrain, canopy = await _gather_geo(bbox)
        fetch_error: str | None = None
    except Exception as exc:  # network, LFPS outage, missing rasterio, etc.
        fuel, terrain, canopy = _empty_geo_payload(str(exc))
        fetch_error = str(exc)

    purity = float(fuel.get("purity", 0.0))
    n_classes = len(fuel.get("class_distribution", {}))
    driver = _confidence_driver(purity, n_classes) if n_classes else (
        f"fuel fetch failed: {fetch_error}" if fetch_error else "no fuel pixels in AOI"
    )

    narrative = await _haiku_narrative(
        incident.model_dump(), fuel, terrain, canopy
    )
    model_called = narrative is not None
    if narrative is None:
        narrative = _stub_narrative(incident.name, fuel, terrain, canopy)

    # Flat projections — contract with spread_simulation._extract_terrain_fuel.
    # Spread expects top-level string `fuel_model` (FBFM40 code), `slope_pct`
    # (degrees converted to percent), and `aspect_deg` (dominant cardinal).
    flat_fuel_code = _dominant_fuel_code(fuel)
    flat_slope_pct = _slope_deg_to_pct(terrain)
    flat_aspect_deg = _dominant_aspect_deg(terrain)

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload={
            # Flat keys: contract with spread_simulation._extract_terrain_fuel.
            "fuel_model": flat_fuel_code,
            "slope_pct": flat_slope_pct,
            "aspect_deg": flat_aspect_deg,
            # Detailed structures for the UI / debugging.
            "bbox": list(bbox),
            "aoi_radius_km": AOI_RADIUS_KM,
            "fuel_detail": fuel,
            "terrain": terrain,
            "canopy": canopy,
            "key_findings": _key_findings(fuel, terrain, canopy),
            "fetch_error": fetch_error,
        },
        confidence=purity if not fetch_error else 0.0,
        confidence_driver=driver,
        citation_bundle=_build_citations(fuel, terrain, canopy, model_called),
    )
    return {"outputs": {AGENT_NAME: output}}


_ASPECT_CARDINAL_DEG = {
    "N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
    "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0,
    "FLAT": 0.0,
}


def _dominant_fuel_code(fuel: dict) -> str:
    """Pull the FBFM40 code from the top-ranked class, or a safe default."""
    classes = fuel.get("dominant_classes") or []
    if classes:
        code = classes[0].get("code")
        if isinstance(code, str) and code:
            return code
    return "GS2"


def _slope_deg_to_pct(terrain: dict) -> float:
    """Convert mean slope in degrees to percent (tan(deg)*100).

    Returns a 15% default when terrain data is missing — same default
    spread_simulation uses internally.
    """
    slope = (terrain.get("slope_deg") or {}) if isinstance(terrain, dict) else {}
    mean_deg = slope.get("mean")
    if mean_deg is None:
        return 15.0
    try:
        import math as _m

        return round(_m.tan(_m.radians(float(mean_deg))) * 100.0, 1)
    except (TypeError, ValueError):
        return 15.0


def _dominant_aspect_deg(terrain: dict) -> float:
    """Pick the dominant aspect cardinal direction and return its centre azimuth.

    Returns 225.0 (SW) by default — matches spread_simulation's hardcoded
    fallback so degraded behavior is deterministic.
    """
    aspect_dist = (
        (terrain.get("aspect_distribution") or {})
        if isinstance(terrain, dict)
        else {}
    )
    if not aspect_dist:
        return 225.0
    top_card, _ = max(aspect_dist.items(), key=lambda kv: kv[1])
    return float(_ASPECT_CARDINAL_DEG.get(str(top_card).upper(), 225.0))


# --------------------------------------------------------------------------- #
# Smoke test — `python -m embersight_agent.agents.terrain_fuel`
# --------------------------------------------------------------------------- #


def _smoke_state() -> AgentState:
    from ..state import Incident
    from ..tools.seed_demo import make_synthetic_incident

    raw = make_synthetic_incident()
    return AgentState(incident=Incident(**raw), operational_period=1)


async def _smoke() -> None:
    state = _smoke_state()
    print(f"[smoke] incident: {state.incident.name} @ "
          f"({state.incident.lat}, {state.incident.lon})")
    patch = await run(state)
    out: AgentOutput = patch["outputs"][AGENT_NAME]
    print(f"[smoke] confidence: {out.confidence:.3f}  ({out.confidence_driver})")
    print(f"[smoke] narrative: {out.narrative[:400]}")
    print(f"[smoke] key findings: {out.payload.get('key_findings')}")
    if out.payload.get("fetch_error"):
        print(f"[smoke] fetch_error: {out.payload['fetch_error']}")


if __name__ == "__main__":
    asyncio.run(_smoke())
