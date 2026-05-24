"""Routing & Staging subagent.

OSMnx + networkx for ingress/egress routes between the incident AOI and
the nearest road network. Scores candidate staging areas on paved access,
water proximity, fire-station support, incident standoff, slope, an
elevation-prominence proxy for radio line-of-sight, and wind alignment
(stage upwind / crosswind of the fire — never downwind).

Depends on ``weather_wind`` (for wind direction → upwind-favoured staging
+ egress) and opportunistically reads ``terrain_fuel`` (for AOI mean
elevation + slope). When those upstream outputs are absent, falls back to
deterministic neutral defaults so the agent never hard-fails.

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
from ..tools.routing import (
    find_routes,
    get_road_network,
    road_density_per_km2,
    score_staging_candidate_detailed,
)

AGENT_NAME = "routing_staging"
MODEL_NAME = "claude-haiku-4-5"
AOI_RADIUS_KM = 25.0
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "routing_staging.md"
)

# Deduplicate candidate locations within this radius — multiple OSM
# features inside a single industrial park / parking complex should
# collapse to one effective staging area.
_DEDUP_RADIUS_KM = 0.4
_MAX_CANDIDATES = 6  # before scoring; final returns top 5
_TOP_N = 5


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


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _ang_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


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
        or tags.get("ref")
        or f"{fallback_prefix}_{feature.get('id', 'unknown')}"
    )


# --------------------------------------------------------------------------- #
# Upstream-output adapters
# --------------------------------------------------------------------------- #


def _wind_from_state(state: AgentState) -> tuple[float | None, float | None]:
    """Return ``(wind_from_deg, wind_speed_mph)`` from the weather_wind
    subagent output, or ``(None, None)`` if unavailable.

    Wind direction uses MET FROM convention. weather_wind exposes it at
    ``payload.wind_dir_deg`` (with a ``payload.critical_window`` override
    for the worst-hour scenario; prefer that when present)."""
    wx = state.outputs.get("weather_wind") if state.outputs else None
    if wx is None:
        return None, None
    payload = wx.payload or {}
    crit = payload.get("critical_window") or {}
    wdir = crit.get("wind_direction_deg")
    wspd = crit.get("wind_speed_mph")
    if wdir is None:
        wdir = payload.get("wind_dir_deg")
    if wspd is None:
        wspd = payload.get("wind_speed_mph")
    try:
        wdir_f = float(wdir) if wdir is not None else None
        wspd_f = float(wspd) if wspd is not None else None
    except (TypeError, ValueError):
        return None, None
    return wdir_f, wspd_f


def _terrain_from_state(state: AgentState) -> dict[str, float | None]:
    """Pull AOI mean elevation (m) and slope (%) from terrain_fuel if
    available. Returns ``{elevation_m, slope_pct}`` with None for missing."""
    tf = state.outputs.get("terrain_fuel") if state.outputs else None
    if tf is None:
        return {"elevation_m": None, "slope_pct": None}
    payload = tf.payload or {}
    slope_pct = payload.get("slope_pct")
    terrain = payload.get("terrain") or {}
    elev = (terrain.get("elevation_m") or {}).get("mean") if isinstance(terrain, dict) else None
    try:
        elev_f = float(elev) if elev is not None and math.isfinite(float(elev)) else None
    except (TypeError, ValueError):
        elev_f = None
    try:
        slope_f = float(slope_pct) if slope_pct is not None else None
    except (TypeError, ValueError):
        slope_f = None
    return {"elevation_m": elev_f, "slope_pct": slope_f}


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #


def _dedup_by_proximity(
    candidates: list[dict[str, Any]], radius_km: float
) -> list[dict[str, Any]]:
    """Greedy spatial dedup. Assumes input is already sorted by preference
    (best first) — keeps the first candidate in each cluster."""
    kept: list[dict[str, Any]] = []
    for c in candidates:
        loc = c.get("loc")
        if not loc:
            continue
        if any(_haversine_km(loc, k["loc"]) < radius_km for k in kept):
            continue
        kept.append(c)
    return kept


def _pick_candidate_locations(
    paved: list[dict[str, Any]],
    water: list[dict[str, Any]],
    incident: tuple[float, float],
    max_n: int = _MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Keep paved features that respect the safety standoff and sit
    within the AOI. Records nearest-water distance for downstream scoring
    but does NOT hard-reject water-sparse candidates (let the score
    penalty handle it — arid AOIs would otherwise have zero recommendations).
    Sorts by (incident distance asc, water distance asc) then dedupes."""
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
        out.append(
            {
                "name": _feature_name(p, "staging"),
                "loc": loc,
                "tags": p.get("tags") or {},
                "dist_incident_km": round(dist_inc, 2),
                "nearest_water_km": (
                    round(nearest_water, 2) if math.isfinite(nearest_water) else None
                ),
            }
        )
    # Sort by composite distance preference (close-but-safe first, then water).
    out.sort(
        key=lambda c: (
            # Penalize the safety-standoff edge so the very-close-but-safe
            # candidates don't dominate over slightly-farther ones with
            # actual headroom (matches the smooth tent in scoring).
            abs((c["dist_incident_km"] or 99) - 5.0),
            c["nearest_water_km"] if c["nearest_water_km"] is not None else 99,
        )
    )
    out = _dedup_by_proximity(out, _DEDUP_RADIUS_KM)
    return out[:max_n]


# --------------------------------------------------------------------------- #
# Egress route computation
# --------------------------------------------------------------------------- #


_EGRESS_BEARINGS_DEG: dict[str, float] = {
    "N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
    "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0,
}

# Minimum standoff from the incident before we'll accept a road node as a
# valid egress endpoint (km). Anything closer is still inside the immediate
# fire-front and not actually "egress".
_EGRESS_MIN_KM = 5.0
_EGRESS_MAX_KM = AOI_RADIUS_KM
_EGRESS_SWEET_KM = 10.0
# How many bearings to actually compute routes for. 8 is overkill for the
# UI; we score all 8 then keep the best _EGRESS_TARGET_COUNT.
_EGRESS_TARGET_COUNT = 5


def _find_egress_endpoint(
    graph: Any,
    incident_latlon: tuple[float, float],
    bearing_deg: float,
) -> tuple[float, float] | None:
    """Pick a major-road node aligned with the requested bearing as an
    egress target. Scores nodes by alignment + a sweet-spot distance
    preference; uses motorway/trunk/primary first then falls back to
    secondary if no major road exists in that bearing."""
    inc_lat, inc_lon = incident_latlon
    cos_lat = math.cos(math.radians(inc_lat))

    # Try major roads first, then widen if nothing qualifies.
    for tier_tags, alignment_floor in (
        ({"motorway", "trunk", "primary"}, 0.30),
        ({"motorway", "trunk", "primary", "secondary"}, 0.20),
        ({"motorway", "trunk", "primary", "secondary", "tertiary"}, 0.10),
    ):
        node_ids: set[Any] = set()
        for u, v, data in graph.edges(data=True):
            highway = data.get("highway")
            if isinstance(highway, list):
                highway = highway[0] if highway else None
            if highway in tier_tags:
                node_ids.add(u)
                node_ids.add(v)
        if not node_ids:
            continue
        endpoint = _best_endpoint_in_tier(
            graph, node_ids, incident_latlon, cos_lat, bearing_deg, alignment_floor
        )
        if endpoint is not None:
            return endpoint
    return None


def _best_endpoint_in_tier(
    graph: Any,
    node_ids: set[Any],
    incident_latlon: tuple[float, float],
    cos_lat: float,
    bearing_deg: float,
    alignment_floor: float,
) -> tuple[float, float] | None:
    inc_lat, inc_lon = incident_latlon
    # Unit vector for the target bearing (compass: 0°=N, 90°=E).
    rad = math.radians(bearing_deg)
    dy_dir = math.cos(rad)  # north component
    dx_dir = math.sin(rad)  # east component
    best_score = -math.inf
    best_loc: tuple[float, float] | None = None
    for nid in node_ids:
        data = graph.nodes[nid]
        node_lat = data.get("y")
        node_lon = data.get("x")
        if node_lat is None or node_lon is None:
            continue
        dy = node_lat - inc_lat
        dx = (node_lon - inc_lon) * cos_lat
        dist_km = math.hypot(dy, dx) * 111.0
        if dist_km < _EGRESS_MIN_KM or dist_km > _EGRESS_MAX_KM:
            continue
        norm = math.hypot(dy, dx) or 1e-9
        alignment = (dy * dy_dir + dx * dx_dir) / norm
        if alignment < alignment_floor:
            continue
        # Sweet-spot ~10 km out; symmetric quadratic-ish penalty.
        dist_penalty = abs(dist_km - _EGRESS_SWEET_KM) / _EGRESS_MAX_KM
        score = alignment - 0.35 * dist_penalty
        if score > best_score:
            best_score = score
            best_loc = (node_lat, node_lon)
    return best_loc


def _rank_bearings_by_wind(
    bearings: dict[str, float], wind_from_deg: float | None
) -> list[tuple[str, float]]:
    """Return ``[(label, bearing_deg), ...]`` ordered so egress AWAY from
    where the fire is heading comes first.

    Fire head heading = (wind_from + 180) mod 360. Best egress points
    AWAY from the head, which is the same direction as wind_from. We
    score each bearing by alignment with the upwind vector."""
    items = list(bearings.items())
    if wind_from_deg is None or not math.isfinite(wind_from_deg):
        # No wind — keep canonical compass order.
        return items
    def _key(item: tuple[str, float]) -> float:
        # Smaller angular difference from the upwind direction → better.
        return _ang_diff_deg(item[1], wind_from_deg)
    items.sort(key=_key)
    return items


def _compute_egress_routes(
    graph: Any,
    incident_latlon: tuple[float, float],
    wind_from_deg: float | None,
    target_count: int = _EGRESS_TARGET_COUNT,
) -> list[dict[str, Any]]:
    """Return up to ``target_count`` outward egress routes.

    Bearings are reordered so routes leaving in the upwind / crosswind
    sectors (i.e. AWAY from the fire-head heading) are computed first
    and surfaced first to the UI.
    """
    if graph is None:
        return []
    routes: list[dict[str, Any]] = []
    ranked = _rank_bearings_by_wind(_EGRESS_BEARINGS_DEG, wind_from_deg)
    for label, bearing in ranked:
        endpoint = _find_egress_endpoint(graph, incident_latlon, bearing)
        if endpoint is None:
            continue
        try:
            candidates = find_routes(graph, incident_latlon, endpoint, k=1)
        except Exception:
            continue
        if not candidates:
            continue
        r = candidates[0]
        # Annotate with a wind-relative tag for UI legend.
        wind_tag = _wind_relation_tag(bearing, wind_from_deg)
        routes.append(
            {
                **r,
                "bearing": label,
                "bearing_deg": bearing,
                "endpoint": list(endpoint),
                "wind_relation": wind_tag,
            }
        )
        if len(routes) >= target_count:
            break
    return routes


def _wind_relation_tag(bearing_deg: float, wind_from_deg: float | None) -> str:
    if wind_from_deg is None or not math.isfinite(wind_from_deg):
        return "unknown"
    offset = _ang_diff_deg(bearing_deg, wind_from_deg)
    if offset <= 45.0:
        return "upwind"      # safest — egress away from fire-head
    if offset <= 135.0:
        return "crosswind"   # acceptable — flanking
    return "downwind"        # AVOID — into fire-head direction


# --------------------------------------------------------------------------- #
# LLM synthesis (optional)
# --------------------------------------------------------------------------- #


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "Routing & staging recommendation. Use RECOMMEND/PROPOSE verbs only."


async def _llm_narrative(payload: dict[str, Any]) -> str | None:
    """Call Haiku 4.5 to draft a one-paragraph recommendation. Returns
    ``None`` if the ANTHROPIC_API_KEY is not set or the call fails —
    caller falls back to a deterministic narrative."""
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
    wind = payload.get("wind") or {}
    upwind_routes = [
        r for r in payload.get("egress_routes", []) if r.get("wind_relation") == "upwind"
    ]
    user = (
        "Recommend a primary incident staging area and its primary ingress route.\n"
        f"Incident: {payload.get('incident_name')} at {payload.get('incident_latlon')}.\n"
        f"OSM features in 25 km AOI: paved={payload.get('counts', {}).get('paved')}, "
        f"water={payload.get('counts', {}).get('water')}, "
        f"fire_stations={payload.get('counts', {}).get('fire_stations')}.\n"
        f"Wind: {wind.get('from_deg')}° at {wind.get('speed_mph')} mph (FROM).\n"
        f"Top candidate (with component scores): {top}.\n"
        f"Best upwind egress route(s): {upwind_routes[:2]}.\n"
        "Output 2-3 sentences, ≤90 words, RECOMMEND/PROPOSE/SUGGEST verbs only. "
        "Mention wind alignment and the safety standoff. "
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
            "egress_routes": [],
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

    # Upstream weather / terrain — present when graph wires this agent
    # downstream of weather_wind + terrain_fuel; None during isolated tests.
    wind_from_deg, wind_speed_mph = _wind_from_state(state)
    terrain = _terrain_from_state(state)
    aoi_elev_m = terrain["elevation_m"]
    aoi_slope_pct = terrain["slope_pct"]

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

    road_density = (
        await asyncio.to_thread(road_density_per_km2, road_graph, bbox)
        if road_graph is not None
        else 0.0
    )

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
        # Elevation: prefer AOI mean from terrain_fuel, else OSM ele tag,
        # else conservative midband default. The default contributes the
        # same to every candidate so it doesn't skew ranking — but the
        # real AOI mean from terrain_fuel makes the comms-LOS axis useful.
        osm_elev = cand.get("tags", {}).get("ele")
        try:
            osm_elev_f = float(osm_elev) if osm_elev is not None else None
        except (TypeError, ValueError):
            osm_elev_f = None
        dem_elev = osm_elev_f if osm_elev_f is not None else (aoi_elev_m or 300.0)

        detail = score_staging_candidate_detailed(
            loc_latlon=cand["loc"],
            paved_areas=paved,
            water=water,
            fire_stations=fire_stations,
            dem_elevation=float(dem_elev),
            incident_latlon=incident_latlon,
            slope_pct=aoi_slope_pct,
            wind_from_deg=wind_from_deg,
        )
        enriched.append(
            {
                **cand,
                "score": detail["total"],
                "score_components": detail["components"],
                "score_weights": detail["weights"],
                "score_raw": detail["raw"],
                "dem_elevation_m": float(dem_elev),
                "routes": routes,
            }
        )

    enriched.sort(key=lambda c: c["score"], reverse=True)
    enriched = enriched[:_TOP_N]
    top = enriched[0] if enriched else None
    primary_routes = top["routes"] if top else []

    # Outward egress routes (incident → nearest major-road node) — ranked
    # so upwind/crosswind egress (away from fire-head) comes first.
    egress_routes: list[dict[str, Any]] = []
    if road_graph is not None:
        try:
            egress_routes = await asyncio.to_thread(
                _compute_egress_routes, road_graph, incident_latlon, wind_from_deg
            )
        except Exception:
            egress_routes = []

    nearest_water_name = None
    nearest_water_km_top: float | None = None
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
        if math.isfinite(best_w_km):
            nearest_water_km_top = round(best_w_km, 2)

    # --- Confidence ---------------------------------------------------------
    # Real drive routes are the dominant signal: if at least one candidate
    # has a primary_routes entry, confidence is floored above 0.6, BUT
    # we additionally discount when the road graph is sparse — recommendations
    # in a 0.3 km/km² AOI shouldn't carry the same weight as a 4 km/km² one.
    have_graph = road_graph is not None
    have_routes = any(c.get("routes") for c in enriched)
    routed_count = sum(1 for c in enriched if c.get("routes"))
    have_stations = len(fire_stations) >= 2
    have_water = len(water) >= 1
    have_candidates = len(enriched) >= 1
    have_wind = wind_from_deg is not None
    have_terrain = aoi_elev_m is not None or aoi_slope_pct is not None
    sparse_roads = have_graph and road_density < 1.5

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
        drivers.append("no paved staging candidates within AOI")
    else:
        confidence -= 0.40
        drivers.append("road graph built but no drivable route to any candidate")

    if sparse_roads:
        confidence -= 0.10
        drivers.append(
            f"sparse road network ({road_density:.1f} km/km²) — recommendations are coarse"
        )
    if not have_stations:
        confidence -= 0.12
        drivers.append(f"only {len(fire_stations)} fire stations in AOI")
    if not have_water:
        confidence -= 0.12
        drivers.append("no water features in AOI")
    if not have_wind:
        confidence -= 0.06
        drivers.append("wind direction unavailable — neutral wind score applied")
    if not have_terrain:
        confidence -= 0.04
        drivers.append("terrain_fuel unavailable — elevation/slope used neutral defaults")
    if osm_failures:
        confidence -= 0.05
        drivers.append(f"Overpass failures: {','.join(osm_failures)}")
    confidence = max(0.05, min(1.0, confidence))
    # Hard floor: if we actually routed against a real road graph, the
    # output is decision-useful even when ancillary layers are thin —
    # but the floor is lower when the network is sparse.
    if have_routes:
        floor = 0.55 if sparse_roads else 0.65
        confidence = max(confidence, floor)

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
        "egress_routes": egress_routes,
        "graph_error": graph_error,
        "osm_failures": osm_failures,
        "road_density_km_per_km2": round(road_density, 2),
        "wind": {
            "from_deg": wind_from_deg,
            "speed_mph": wind_speed_mph,
            "source": "weather_wind" if have_wind else None,
        },
        "terrain": {
            "aoi_elevation_m": aoi_elev_m,
            "aoi_slope_pct": aoi_slope_pct,
            "source": "terrain_fuel" if have_terrain else None,
        },
    }

    llm_text = await _llm_narrative(payload)
    if llm_text:
        narrative = llm_text
    elif top:
        wind_note = ""
        if have_wind:
            wind_tag = _wind_relation_tag(
                _bearing_deg(incident_latlon, top["loc"]), wind_from_deg
            )
            wind_note = f" Candidate sits {wind_tag} of the fire."
        narrative = (
            f"[{AGENT_NAME}] RECOMMEND staging at '{top['name']}' "
            f"({top['loc'][0]:.4f},{top['loc'][1]:.4f}) — {top['dist_incident_km']} km "
            f"from incident, nearest water {top.get('nearest_water_km', 'n/a')} km away, "
            f"composite score {top['score']:.2f}.{wind_note} PROPOSE primary ingress via "
            f"the shortest of {len(primary_routes)} candidate routes."
        )
    else:
        narrative = (
            f"[{AGENT_NAME}] SUGGEST manual staging selection — no paved area "
            f"in the 25 km AOI satisfied the 2 km safety standoff."
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
    if nearest_water_name and nearest_water_km_top is not None:
        key_findings.append(
            f"Water source: {nearest_water_name} ({nearest_water_km_top} km)"
        )
    upwind_egress = [r for r in egress_routes if r.get("wind_relation") == "upwind"]
    if upwind_egress:
        be = upwind_egress[0]
        key_findings.append(
            f"Primary egress: bearing {be['bearing']} (upwind), "
            f"{be['length_km']} km / {be['est_drive_minutes']} min"
        )
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
