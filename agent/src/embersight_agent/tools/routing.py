"""OSMnx + networkx ingress/egress routing and staging-area scoring.

All public callables here are synchronous (OSMnx is CPU/IO-bound and not
asyncio-aware). The async agent should invoke them via ``asyncio.to_thread``.
The heavyweight imports (``osmnx``, ``networkx``, ``shapely``) are deferred
to call time so pass-1 environments without the ``science`` dep group can
still import the module.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any

# Cache dir is overridable so deployments outside ephemeral /tmp can pin it.
CACHE_DIR = Path(os.environ.get("EMBERSIGHT_OSMNX_CACHE", "/tmp/osmnx-cache"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _bbox_hash(bbox: tuple[float, float, float, float], network_type: str) -> str:
    raw = f"{bbox[0]:.5f},{bbox[1]:.5f},{bbox[2]:.5f},{bbox[3]:.5f}|{network_type}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


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
    """Initial compass bearing in degrees from ``a`` to ``b`` (0=N, 90=E)."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _ang_diff_deg(a: float, b: float) -> float:
    """Shortest absolute angular difference in degrees (0..180)."""
    d = abs((a - b + 180.0) % 360.0 - 180.0)
    return d


# --------------------------------------------------------------------------- #
# Road network
# --------------------------------------------------------------------------- #


def get_road_network(
    bbox: tuple[float, float, float, float],
    network_type: str = "drive",
) -> Any:
    """Return an OSMnx street graph for ``bbox = (south, west, north, east)``.

    Cached to ``CACHE_DIR/<bbox-hash>.graphml`` so repeated runs in
    a session don't hammer Overpass. Caller is responsible for handling
    network failures — we propagate.
    """
    import osmnx as ox  # type: ignore[import-not-found]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ox.settings.cache_folder = str(CACHE_DIR / "overpass-json")
    except Exception:
        pass
    cache_path = CACHE_DIR / f"{_bbox_hash(bbox, network_type)}.graphml"
    if cache_path.exists():
        try:
            return ox.load_graphml(cache_path)
        except Exception:
            # Corrupt cache — drop it and re-fetch.
            try:
                cache_path.unlink()
            except OSError:
                pass

    south, west, north, east = bbox
    # OSMnx 2.x expects bbox = (left, bottom, right, top) i.e. (W, S, E, N).
    graph = ox.graph_from_bbox(
        bbox=(west, south, east, north),
        network_type=network_type,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )
    try:
        ox.save_graphml(graph, cache_path)
    except Exception:
        pass
    return graph


def road_density_per_km2(
    graph: Any,
    bbox: tuple[float, float, float, float],
) -> float:
    """Total drivable edge length / bbox area, in km/km². Used by the
    routing agent's confidence model — sparse AOIs should be flagged."""
    if graph is None:
        return 0.0
    south, west, north, east = bbox
    sw, ne = (south, west), (north, east)
    # Rough rectangular area: side1 * side2 in km.
    side_lat = _haversine_km((south, west), (north, west))
    side_lon = _haversine_km((south, west), (south, east))
    area = max(0.01, side_lat * side_lon)
    total_m = 0.0
    for _, _, data in graph.edges(data=True):
        total_m += float(data.get("length", 0.0))
    return (total_m / 1000.0) / area


# --------------------------------------------------------------------------- #
# Route search
# --------------------------------------------------------------------------- #


def _to_simple_digraph(graph: Any, nx: Any) -> Any:
    """Collapse a MultiDiGraph to DiGraph keeping minimum-length parallel edges."""
    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes(data=True))
    for u, v, data in graph.edges(data=True):
        existing = simple.get_edge_data(u, v)
        w = float(data.get("length", 1e9))
        if existing is None or w < existing.get("length", 1e9):
            simple.add_edge(u, v, **data)
    return simple


# Cache simple digraphs by source-graph identity to avoid rebuilding on every
# find_routes call (5 candidates × 4 egress = 9 calls per briefing).
_SIMPLE_CACHE: dict[int, Any] = {}


def _simple_for(graph: Any, nx: Any) -> Any:
    key = id(graph)
    cached = _SIMPLE_CACHE.get(key)
    if cached is not None:
        return cached
    simple = _to_simple_digraph(graph, nx)
    # Cap cache to avoid leaks across long-running processes with many AOIs.
    if len(_SIMPLE_CACHE) > 8:
        _SIMPLE_CACHE.pop(next(iter(_SIMPLE_CACHE)))
    _SIMPLE_CACHE[key] = simple
    return simple


def _nearest_node(graph: Any, latlon: tuple[float, float], ox: Any) -> Any:
    """Find nearest OSMnx node to latlon.

    Tries ox.nearest_nodes first (fast, needs scikit-learn).
    Falls back to a brute-force O(n) scan over lat/cos-adjusted lon so
    the function works without scikit-learn installed.
    """
    lat, lon = latlon
    try:
        return ox.nearest_nodes(graph, X=lon, Y=lat)
    except ImportError:
        pass
    best_node = None
    best_sq = math.inf
    cos_lat = math.cos(math.radians(lat))
    for nid, data in graph.nodes(data=True):
        dy = data["y"] - lat
        dx = (data["x"] - lon) * cos_lat
        sq = dy * dy + dx * dx
        if sq < best_sq:
            best_sq = sq
            best_node = nid
    return best_node


def find_routes(
    graph: Any,
    origin_latlon: tuple[float, float],
    dest_latlon: tuple[float, float],
    k: int = 3,
) -> list[dict[str, Any]]:
    """Return the k shortest simple paths from origin to destination.

    Each entry: ``{path, length_km, est_drive_minutes, avg_speed_kph,
    bearing_deg}``. On any networkx failure (no path, missing nodes)
    returns ``[]``.
    """
    import networkx as nx  # type: ignore[import-not-found]
    import osmnx as ox  # type: ignore[import-not-found]

    try:
        orig = _nearest_node(graph, origin_latlon, ox)
        dest = _nearest_node(graph, dest_latlon, ox)
    except Exception:
        return []
    if orig is None or dest is None or orig == dest:
        return []

    simple = _simple_for(graph, nx)
    try:
        gen = nx.shortest_simple_paths(simple, orig, dest, weight="length")
    except (nx.NodeNotFound, nx.NetworkXNoPath, nx.NetworkXError):
        return []

    routes: list[dict[str, Any]] = []
    for idx, node_path in enumerate(gen):
        if idx >= k:
            break
        coords: list[tuple[float, float]] = []
        length_m = 0.0
        weighted_speed = 0.0
        weighted_len = 0.0
        for i, node in enumerate(node_path):
            data = simple.nodes[node]
            coords.append((data["y"], data["x"]))
            if i == 0:
                continue
            prev = node_path[i - 1]
            edge_data = simple.get_edge_data(prev, node)
            if not edge_data:
                continue
            seg_len = float(edge_data.get("length", 0.0))
            length_m += seg_len
            maxspeed = edge_data.get("maxspeed")
            kph = _parse_maxspeed(maxspeed) or _default_speed_kph(
                edge_data.get("highway")
            )
            weighted_speed += kph * seg_len
            weighted_len += seg_len
        avg_kph = (weighted_speed / weighted_len) if weighted_len else 40.0
        length_km = length_m / 1000.0
        est_minutes = (length_km / max(avg_kph, 1.0)) * 60.0
        bearing = _bearing_deg(coords[0], coords[-1]) if len(coords) >= 2 else 0.0
        routes.append(
            {
                "path": coords,
                "length_km": round(length_km, 3),
                "est_drive_minutes": round(est_minutes, 1),
                "avg_speed_kph": round(avg_kph, 1),
                "bearing_deg": round(bearing, 1),
            }
        )
    return routes


def _parse_maxspeed(value: Any) -> float | None:
    """Parse an OSM ``maxspeed`` tag into KPH. Tolerant of lists, units,
    and ``signals``/``walk``/``none`` sentinels."""
    if value is None:
        return None
    if isinstance(value, list):
        # Pick the largest parseable speed in the list (conservative for
        # divided roads where the higher-speed direction is the relevant one).
        parsed = [v for v in (_parse_maxspeed(x) for x in value) if v is not None]
        return max(parsed) if parsed else None
    try:
        s = str(value).strip().lower()
        if not s or s in {"none", "signals", "walk", "variable"}:
            return None
        # Take the first numeric token; handle "50 mph", "50mph", "80 km/h", "80".
        head = s.split(";")[0].split(",")[0].strip()
        if "mph" in head:
            num = head.replace("mph", "").strip()
            return float(num) * 1.60934
        if "knots" in head:
            num = head.replace("knots", "").strip()
            return float(num) * 1.852
        # Drop trailing "km/h" / "kph" / unit tokens.
        for tok in (" km/h", "km/h", " kph", "kph"):
            if head.endswith(tok):
                head = head[: -len(tok)].strip()
        return float(head.split()[0])
    except Exception:
        return None


def _default_speed_kph(highway: Any) -> float:
    if isinstance(highway, list):
        highway = highway[0] if highway else None
    defaults = {
        "motorway": 100.0,
        "motorway_link": 70.0,
        "trunk": 80.0,
        "trunk_link": 55.0,
        "primary": 65.0,
        "primary_link": 45.0,
        "secondary": 55.0,
        "secondary_link": 40.0,
        "tertiary": 45.0,
        "tertiary_link": 35.0,
        "unclassified": 40.0,
        "residential": 35.0,
        "service": 25.0,
        "track": 20.0,
        "living_street": 15.0,
    }
    return defaults.get(str(highway or ""), 40.0)


# --------------------------------------------------------------------------- #
# Staging scoring
# --------------------------------------------------------------------------- #

# Tunables. Documented inline so the IC can audit how the score was built.
# Weights sum to 1.0.
_W_INCIDENT = 0.28   # close-but-safe standoff
_W_WATER = 0.22      # nearest water source
_W_STATION = 0.16    # nearest fire station (mutual-aid + apparatus)
_W_PAVED = 0.10      # surface usability
_W_ELEV = 0.08       # comms LOS proxy (only meaningful with real DEM)
_W_SLOPE = 0.08      # gentle ground for parking apparatus
_W_WIND = 0.08       # upwind/crosswind preference (smoke + flank-fire risk)
_SAFE_STANDOFF_KM = 2.0
_INC_PEAK_KM = 5.0    # sweet spot for staging-to-incident distance
_INC_FALLOFF_KM = 15.0  # score reaches zero past this
_WATER_SOFT_KM = 8.0    # water proximity decays to 0 here (not hard-reject)
_STATION_SOFT_KM = 12.0
_PAVED_SOFT_KM = 2.0
_ELEV_REF_M = 1500.0
# Slope hurts staging: 0% great, 15% marginal, 25% unusable.
_SLOPE_OK_PCT = 8.0
_SLOPE_HARD_PCT = 25.0


def _nearest_km(loc: tuple[float, float], features: list[dict[str, Any]]) -> float:
    best = math.inf
    for f in features:
        latlon = _feature_latlon(f)
        if latlon is None:
            continue
        d = _haversine_km(loc, latlon)
        if d < best:
            best = d
    return best


def _feature_latlon(feature: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in feature and "lon" in feature:
        return float(feature["lat"]), float(feature["lon"])
    c = feature.get("center")
    if isinstance(c, dict) and "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    return None


def _incident_distance_score(inc_km: float) -> float:
    """Smooth tent: 0 at standoff, peak at _INC_PEAK_KM, 0 at _INC_FALLOFF_KM.

    Replaces the original discontinuous (0 → 0.7 at the standoff boundary).
    Inside the safety standoff we still hard-zero — staging adjacent to the
    fire front is unsafe regardless of every other axis.
    """
    if inc_km < _SAFE_STANDOFF_KM:
        return 0.0
    if inc_km <= _INC_PEAK_KM:
        # Linear rise 0 → 1 from standoff to peak.
        span = _INC_PEAK_KM - _SAFE_STANDOFF_KM
        return (inc_km - _SAFE_STANDOFF_KM) / span if span > 0 else 1.0
    if inc_km <= _INC_FALLOFF_KM:
        span = _INC_FALLOFF_KM - _INC_PEAK_KM
        return 1.0 - (inc_km - _INC_PEAK_KM) / span if span > 0 else 0.0
    return 0.0


def _slope_score(slope_pct: float | None) -> float:
    """Gentle ground gets ~1; steep ground gets ~0. Linear decay from
    _SLOPE_OK_PCT to _SLOPE_HARD_PCT."""
    if slope_pct is None or not math.isfinite(slope_pct):
        return 0.5  # neutral when unknown
    if slope_pct <= _SLOPE_OK_PCT:
        return 1.0
    if slope_pct >= _SLOPE_HARD_PCT:
        return 0.0
    span = _SLOPE_HARD_PCT - _SLOPE_OK_PCT
    return max(0.0, 1.0 - (slope_pct - _SLOPE_OK_PCT) / span)


def _wind_score(
    candidate_latlon: tuple[float, float],
    incident_latlon: tuple[float, float],
    wind_from_deg: float | None,
) -> float:
    """Reward staging upwind or crosswind of the incident.

    Wind direction uses MET FROM convention. The fire HEAD advances toward
    ``(wind_from + 180) mod 360``. We compute the bearing from the incident
    to the candidate and compare it to the wind FROM direction:

    - 0° offset  → candidate is directly upwind          → score 1.0
    - 90° offset → candidate is crosswind (flank)        → score 0.55
    - 180° off   → candidate is directly downwind (bad)  → score 0.0
    """
    if wind_from_deg is None or not math.isfinite(wind_from_deg):
        return 0.5  # neutral when unknown
    # Bearing from incident to candidate. If wind blows FROM 270° (W), the
    # candidate is "upwind" when it sits to the W of the incident, i.e.
    # bearing ≈ 270°.
    bearing = _bearing_deg(incident_latlon, candidate_latlon)
    offset = _ang_diff_deg(bearing, wind_from_deg)  # 0..180
    # Cosine-ish curve: 1.0 upwind, ~0.5 crosswind, 0.0 downwind.
    return max(0.0, (1.0 + math.cos(math.radians(offset))) / 2.0)


def score_staging_candidate(
    loc_latlon: tuple[float, float],
    paved_areas: list[dict[str, Any]],
    water: list[dict[str, Any]],
    fire_stations: list[dict[str, Any]],
    dem_elevation: float,
    incident_latlon: tuple[float, float],
    *,
    slope_pct: float | None = None,
    wind_from_deg: float | None = None,
) -> float:
    """Composite 0..1 staging-area score (backwards-compatible scalar)."""
    return score_staging_candidate_detailed(
        loc_latlon=loc_latlon,
        paved_areas=paved_areas,
        water=water,
        fire_stations=fire_stations,
        dem_elevation=dem_elevation,
        incident_latlon=incident_latlon,
        slope_pct=slope_pct,
        wind_from_deg=wind_from_deg,
    )["total"]


def score_staging_candidate_detailed(
    loc_latlon: tuple[float, float],
    paved_areas: list[dict[str, Any]],
    water: list[dict[str, Any]],
    fire_stations: list[dict[str, Any]],
    dem_elevation: float,
    incident_latlon: tuple[float, float],
    *,
    slope_pct: float | None = None,
    wind_from_deg: float | None = None,
) -> dict[str, Any]:
    """Composite 0..1 staging-area score with per-component breakdown.

    Components (each in 0..1, weighted to sum 1.0):

    - **incident distance** — smooth tent: 0 inside the 2 km safety
      standoff, linear rise to a peak at 5 km, linear decay to 0 at 15 km.
    - **water proximity** — nearest water feature; ≤8 km maps to 1..0.
    - **fire-station proximity** — nearest fire station within 12 km maps
      to 1..0 (was 10 — relaxed since stations are sparse in rural AOIs).
    - **paved proximity** — nearest paved feature within 2 km maps to 1..0.
    - **elevation prominence** — 0..1 normalized against a 1500 m reference;
      used as a comms-LOS proxy. Only contributes meaningfully when real
      DEM data is wired (e.g. from terrain_fuel.elevation_m.mean).
    - **slope** — gentle ground (≤8%) scores 1.0; decays to 0 at 25%.
    - **wind alignment** — upwind candidate scores 1.0; crosswind 0.55;
      downwind 0.0. Neutral 0.5 if wind unknown.
    """
    inc_km = _haversine_km(loc_latlon, incident_latlon)
    incident_score = _incident_distance_score(inc_km)

    water_km = _nearest_km(loc_latlon, water)
    water_score = (
        max(0.0, 1.0 - water_km / _WATER_SOFT_KM) if math.isfinite(water_km) else 0.0
    )

    station_km = _nearest_km(loc_latlon, fire_stations)
    station_score = (
        max(0.0, 1.0 - station_km / _STATION_SOFT_KM)
        if math.isfinite(station_km)
        else 0.0
    )

    paved_km = _nearest_km(loc_latlon, paved_areas)
    paved_score = (
        max(0.0, 1.0 - paved_km / _PAVED_SOFT_KM) if math.isfinite(paved_km) else 0.0
    )

    elev_score = max(0.0, min(1.0, float(dem_elevation) / _ELEV_REF_M))
    slope_s = _slope_score(slope_pct)
    wind_s = _wind_score(loc_latlon, incident_latlon, wind_from_deg)

    total = (
        _W_INCIDENT * incident_score
        + _W_WATER * water_score
        + _W_STATION * station_score
        + _W_PAVED * paved_score
        + _W_ELEV * elev_score
        + _W_SLOPE * slope_s
        + _W_WIND * wind_s
    )
    return {
        "total": round(total, 4),
        "components": {
            "incident": round(incident_score, 3),
            "water": round(water_score, 3),
            "station": round(station_score, 3),
            "paved": round(paved_score, 3),
            "elevation": round(elev_score, 3),
            "slope": round(slope_s, 3),
            "wind": round(wind_s, 3),
        },
        "weights": {
            "incident": _W_INCIDENT,
            "water": _W_WATER,
            "station": _W_STATION,
            "paved": _W_PAVED,
            "elevation": _W_ELEV,
            "slope": _W_SLOPE,
            "wind": _W_WIND,
        },
        "raw": {
            "incident_km": round(inc_km, 3),
            "water_km": round(water_km, 3) if math.isfinite(water_km) else None,
            "station_km": round(station_km, 3) if math.isfinite(station_km) else None,
            "paved_km": round(paved_km, 3) if math.isfinite(paved_km) else None,
            "elevation_m": round(float(dem_elevation), 1),
            "slope_pct": slope_pct,
            "wind_from_deg": wind_from_deg,
        },
    }
