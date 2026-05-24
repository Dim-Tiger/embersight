"""Routing & Staging subagent.

OSMnx + networkx for ingress/egress routes between the incident AOI and
the nearest road network. Scores candidate staging areas on paved access,
water proximity, fire-station support, incident standoff, and an
elevation-prominence proxy for radio line-of-sight.

This agent is informational — it RECOMMENDS / PROPOSES / SUGGESTS only.
It never dispatches resources, opens orders, or sends to external systems,
and it never raises an interrupt.
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
    Model,
)
from ..tools.overpass import (
    OVERPASS,
    get_fire_stations,
    get_paved_areas,
    get_water_features,
    query_osm,
)
from ..tools.routing import find_routes, get_road_network, score_staging_candidate

AGENT_NAME = "routing_staging"
MODEL_NAME = "claude-haiku-4-5"
AOI_RADIUS_KM = 25.0
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "routing_staging.md"
)


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _bbox_around(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _feature_latlon(feature: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in feature and "lon" in feature:
        return float(feature["lat"]), float(feature["lon"])
    c = feature.get("center")
    if isinstance(c, dict) and "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    return None


def _feature_name(feature: dict[str, Any], fallback_prefix: str) -> str:
    tags = feature.get("tags") or {}
    return (
        tags.get("name")
        or tags.get("operator")
        or f"{fallback_prefix}_{feature.get('id', 'unknown')}"
    )


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #


def _pick_candidate_locations(
    paved: list[dict[str, Any]],
    water: list[dict[str, Any]],
    incident: tuple[float, float],
    max_n: int = 5,
) -> list[dict[str, Any]]:
    """Heuristic: keep paved features that respect the 2 km safety standoff,
    sit within the AOI, and have at least one water feature within 5 km.
    Sort by (water distance asc, incident distance asc)."""
    out: list[dict[str, Any]] = []
    for p in paved:
        loc = _feature_latlon(p)
        if loc is None:
            continue
        dist_inc = _haversine_km(loc, incident)
        if dist_inc < 2.0 or dist_inc > AOI_RADIUS_KM:
            continue
        nearest_water = math.inf
        for w in water:
            wloc = _feature_latlon(w)
            if wloc is None:
                continue
            d = _haversine_km(loc, wloc)
            if d < nearest_water:
                nearest_water = d
        if nearest_water > 5.0:
            continue
        out.append(
            {
                "name": _feature_name(p, "staging"),
                "loc": loc,
                "tags": p.get("tags") or {},
                "dist_incident_km": round(dist_inc, 2),
                "nearest_water_km": round(nearest_water, 2)
                if math.isfinite(nearest_water)
                else None,
            }
        )
    out.sort(key=lambda c: (c["nearest_water_km"] or 99, c["dist_incident_km"]))
    return out[:max_n]


# --------------------------------------------------------------------------- #
# LLM synthesis (optional)
# --------------------------------------------------------------------------- #


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "Routing & staging recommendation. Use RECOMMEND/PROPOSE verbs only."


async def _llm_narrative(payload: dict[str, Any]) -> str | None:
    """Call Haiku 4.5 to draft a one-paragraph recommendation. Returns None
    if the ANTHROPIC_API_KEY is not set or the call fails — caller falls
    back to a deterministic narrative."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage
    except Exception:
        return None
    system_prompt = _load_prompt()
    candidates = payload.get("candidates", [])
    top = candidates[0] if candidates else None
    user = (
        "Recommend a primary incident staging area and its primary ingress route.\n"
        f"Incident: {payload.get('incident_name')} at {payload.get('incident_latlon')}.\n"
        f"OSM features in 25 km AOI: paved={payload.get('counts', {}).get('paved')}, "
        f"water={payload.get('counts', {}).get('water')}, "
        f"fire_stations={payload.get('counts', {}).get('fire_stations')}.\n"
        f"Top candidate: {top}.\n"
        f"Routes for top candidate: {payload.get('primary_routes')}.\n"
        "Output 2-3 sentences, ≤90 words, RECOMMEND/PROPOSE/SUGGEST verbs only. "
        "Never use directive verbs like 'dispatch', 'order', 'send', 'publish'."
    )
    try:
        from ..tools.llm_stream import stream_text
        llm = ChatAnthropic(model=MODEL_NAME, max_tokens=400, temperature=0.2)
        content = await stream_text(
            llm,
            [SystemMessage(content=system_prompt), HumanMessage(content=user)],
        )
        return content.strip() if content else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #


def _stub_output(state: AgentState, reason: str) -> AgentOutput:
    incident = state.incident
    name = incident.name if incident else "unknown-incident"
    return AgentOutput(
        agent=AGENT_NAME,
        narrative=(
            f"[{AGENT_NAME}] PROPOSE deterministic staging fallback for {name}: "
            "no OSM data could be fetched, so no live recommendation is "
            "available. SUGGEST manual selection by ICS Planning."
        ),
        payload={
            "stub": True,
            "reason": reason,
            "candidates": [],
            "primary_routes": [],
        },
        confidence=0.1,
        confidence_driver=f"degraded: {reason}",
        citation_bundle=CitationBundle(
            datasets=[Dataset(name="OSM Overpass (unavailable)", version="-")],
            models=[Model(name=MODEL_NAME, version="2025-10")],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )


async def run(state: AgentState) -> dict:
    incident = state.incident
    if incident is None:
        return {
            "outputs": {
                AGENT_NAME: _stub_output(state, "no incident on state")
            }
        }

    incident_latlon = (incident.lat, incident.lon)
    bbox = _bbox_around(incident.lat, incident.lon, AOI_RADIUS_KM)

    # Concurrent Overpass pulls.
    water_t, stations_t, paved_t, highways_t = await asyncio.gather(
        get_water_features(bbox),
        get_fire_stations(bbox),
        get_paved_areas(bbox),
        query_osm(bbox, {"highway": ["primary", "secondary", "tertiary"]}),
        return_exceptions=True,
    )

    def _ok(x: Any) -> list[dict[str, Any]]:
        return x if isinstance(x, list) else []

    water = _ok(water_t)
    fire_stations = _ok(stations_t)
    paved = _ok(paved_t)
    highways = _ok(highways_t)
    osm_failures = [
        kind
        for kind, val in (
            ("water", water_t),
            ("fire_stations", stations_t),
            ("paved", paved_t),
            ("highways", highways_t),
        )
        if isinstance(val, BaseException)
    ]

    if not (water or fire_stations or paved or highways):
        return {
            "outputs": {AGENT_NAME: _stub_output(state, "all Overpass calls failed")}
        }

    candidates = _pick_candidate_locations(paved, water, incident_latlon)

    # Road graph + per-candidate routes (sync OSMnx work, offloaded).
    road_graph: Any = None
    graph_error: str | None = None
    try:
        road_graph = await asyncio.to_thread(get_road_network, bbox, "drive")
    except Exception as exc:  # pragma: no cover - depends on network/osmnx
        graph_error = f"{type(exc).__name__}: {exc}"

    enriched: list[dict[str, Any]] = []
    for cand in candidates:
        routes: list[dict[str, Any]] = []
        if road_graph is not None:
            try:
                routes = await asyncio.to_thread(
                    find_routes, road_graph, cand["loc"], incident_latlon, 3
                )
            except Exception:
                routes = []
        # Elevation proxy — no DEM in pass 2, so use a constant baseline.
        # Real impl will source a SRTM/3DEP tile via terrain_fuel.
        dem_elev = float(cand.get("tags", {}).get("ele", 300.0) or 300.0)
        score = score_staging_candidate(
            loc_latlon=cand["loc"],
            paved_areas=paved,
            water=water,
            fire_stations=fire_stations,
            dem_elevation=dem_elev,
            incident_latlon=incident_latlon,
        )
        enriched.append(
            {
                **cand,
                "score": score,
                "dem_elevation_m": dem_elev,
                "routes": routes,
            }
        )

    enriched.sort(key=lambda c: c["score"], reverse=True)
    top = enriched[0] if enriched else None
    primary_routes = top["routes"] if top else []
    nearest_water_name = None
    if top:
        best_w_km = math.inf
        for w in water:
            wloc = _feature_latlon(w)
            if not wloc:
                continue
            d = _haversine_km(top["loc"], wloc)
            if d < best_w_km:
                best_w_km = d
                nearest_water_name = _feature_name(w, "water")

    # --- Confidence ---------------------------------------------------------
    # Real drive routes are the dominant signal: if at least one candidate
    # has a primary_routes entry, confidence is floored above 0.6.
    have_graph = road_graph is not None
    have_routes = any(c.get("routes") for c in enriched)
    routed_count = sum(1 for c in enriched if c.get("routes"))
    have_stations = len(fire_stations) >= 2
    have_water = len(water) >= 1
    have_candidates = len(enriched) >= 1
    drivers: list[str] = []
    confidence = 1.0
    if have_routes:
        drivers.append(
            f"drive routes computed for {routed_count}/{len(enriched)} candidate(s)"
        )
    elif not have_graph:
        confidence -= 0.40
        drivers.append(
            f"road graph unavailable ({graph_error or 'unknown error'})"
        )
    elif not have_candidates:
        confidence -= 0.40
        drivers.append("no paved+water staging candidates within AOI")
    else:
        confidence -= 0.40
        drivers.append("road graph built but no drivable route to any candidate")
    if not have_stations:
        confidence -= 0.15
        drivers.append(f"only {len(fire_stations)} fire stations in AOI")
    if not have_water:
        confidence -= 0.15
        drivers.append("no water features in AOI")
    if osm_failures:
        confidence -= 0.05
        drivers.append(f"Overpass failures: {','.join(osm_failures)}")
    confidence = max(0.05, min(1.0, confidence))
    # Hard floor: if we actually routed against a real road graph, the
    # output is decision-useful even when ancillary layers are thin.
    if have_routes:
        confidence = max(confidence, 0.65)

    # --- Payload + narrative ------------------------------------------------
    payload = {
        "incident_name": incident.name,
        "incident_latlon": incident_latlon,
        "bbox_25km": bbox,
        "counts": {
            "water": len(water),
            "fire_stations": len(fire_stations),
            "paved": len(paved),
            "highways": len(highways),
            "candidates": len(enriched),
        },
        "candidates": enriched,
        "primary_routes": primary_routes,
        "graph_error": graph_error,
        "osm_failures": osm_failures,
    }

    llm_text = await _llm_narrative(payload)
    if llm_text:
        narrative = llm_text
    elif top:
        narrative = (
            f"[{AGENT_NAME}] RECOMMEND staging at '{top['name']}' "
            f"({top['loc'][0]:.4f},{top['loc'][1]:.4f}) — {top['dist_incident_km']} km "
            f"from incident, nearest water {top['nearest_water_km']} km away, "
            f"composite score {top['score']:.2f}. PROPOSE primary ingress via "
            f"the shortest of {len(primary_routes)} candidate routes."
        )
    else:
        narrative = (
            f"[{AGENT_NAME}] SUGGEST manual staging selection — no candidate paved "
            f"area in the 25 km AOI met the 2 km safety standoff with water "
            f"within 5 km."
        )

    key_findings: list[str] = []
    if top:
        key_findings.append(
            f"Recommended staging: {top['name']} @ "
            f"{top['loc'][0]:.5f},{top['loc'][1]:.5f}"
        )
    if primary_routes:
        first = primary_routes[0]
        key_findings.append(
            f"Primary ingress: {first['length_km']} km / "
            f"{first['est_drive_minutes']} min est."
        )
    if nearest_water_name:
        key_findings.append(f"Water source: {nearest_water_name}")
    payload["key_findings"] = key_findings

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload=payload,
        confidence=round(confidence, 3),
        confidence_driver="; ".join(drivers),
        citation_bundle=CitationBundle(
            datasets=[
                Dataset(
                    name="OSM Overpass",
                    version="overpass-api.de",
                    url=OVERPASS,
                ),
                Dataset(
                    name="OSMnx road graph",
                    version="network_type=drive,radius_km=25",
                ),
            ],
            models=[Model(name=MODEL_NAME, version="2025-10")],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )
    return {"outputs": {AGENT_NAME: output}}


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _los_padres_fixture() -> AgentState:
    """Los Padres NF, somewhere in the Santa Ynez range, for smoke testing."""
    from ..state import Incident

    incident = Incident(
        id="smoke-los-padres",
        name="Los Padres Smoke Test",
        lat=34.5402,
        lon=-119.7796,
        acres=120.0,
        contained_pct=0.0,
        source="synthetic",
    )
    return AgentState(incident=incident, operational_period=1)


async def _smoke() -> None:
    state = _los_padres_fixture()
    try:
        patch = await asyncio.wait_for(run(state), timeout=180.0)
    except asyncio.TimeoutError:
        print(f"[{AGENT_NAME}] smoke test timed out — degraded environment")
        return
    out = patch["outputs"][AGENT_NAME]
    print(f"[{AGENT_NAME}] confidence={out.confidence} driver={out.confidence_driver}")
    print(f"[{AGENT_NAME}] narrative: {out.narrative}")
    counts = out.payload.get("counts", {})
    print(f"[{AGENT_NAME}] counts={counts}")
    for kf in out.payload.get("key_findings", []):
        print(f"  - {kf}")


if __name__ == "__main__":
    asyncio.run(_smoke())
