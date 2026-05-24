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

CACHE_DIR = Path("/tmp/osmnx-cache")


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


# --------------------------------------------------------------------------- #
# Road network
# --------------------------------------------------------------------------- #


def get_road_network(
    bbox: tuple[float, float, float, float],
    network_type: str = "drive",
) -> Any:
    """Return an OSMnx street graph for ``bbox = (south, west, north, east)``.

    Cached to ``/tmp/osmnx-cache/<bbox-hash>.graphml`` so repeated runs in
    a session don't hammer Overpass. Caller is responsible for handling
    network failures — we propagate.
    """
    import osmnx as ox  # type: ignore[import-not-found]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep osmnx's intermediate JSON cache in the same place so cwd stays clean.
    try:
        ox.settings.cache_folder = str(CACHE_DIR / "overpass-json")
    except Exception:
        pass
    cache_path = CACHE_DIR / f"{_bbox_hash(bbox, network_type)}.graphml"
    if cache_path.exists():
        return ox.load_graphml(cache_path)

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
        # Cache errors are non-fatal; the graph is still usable.
        pass
    return graph


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

    Each entry: ``{path: [(lat, lon), ...], length_km, est_drive_minutes}``.
    On any networkx failure (no path, missing nodes) returns ``[]``.
    """
    import networkx as nx  # type: ignore[import-not-found]
    import osmnx as ox  # type: ignore[import-not-found]

    try:
        orig = _nearest_node(graph, origin_latlon, ox)
        dest = _nearest_node(graph, dest_latlon, ox)
    except Exception:
        return []

    # shortest_simple_paths doesn't support MultiDiGraph; collapse to DiGraph.
    simple = _to_simple_digraph(graph, nx)
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
            kph = _parse_maxspeed(maxspeed) or _default_speed_kph(edge_data.get("highway"))
            weighted_speed += kph * seg_len
            weighted_len += seg_len
        avg_kph = (weighted_speed / weighted_len) if weighted_len else 40.0
        length_km = length_m / 1000.0
        est_minutes = (length_km / max(avg_kph, 1.0)) * 60.0
        routes.append(
            {
                "path": coords,
                "length_km": round(length_km, 3),
                "est_drive_minutes": round(est_minutes, 1),
            }
        )
    return routes


def _parse_maxspeed(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
        if value is None:
            return None
    try:
        s = str(value).strip().lower()
        if s.endswith("mph"):
            return float(s.replace("mph", "").strip()) * 1.60934
        return float(s.split()[0])
    except Exception:
        return None


def _default_speed_kph(highway: Any) -> float:
    if isinstance(highway, list):
        highway = highway[0] if highway else None
    defaults = {
        "motorway": 100.0,
        "trunk": 80.0,
        "primary": 65.0,
        "secondary": 55.0,
        "tertiary": 45.0,
        "unclassified": 40.0,
        "residential": 35.0,
        "service": 25.0,
        "track": 20.0,
    }
    return defaults.get(str(highway or ""), 40.0)


# --------------------------------------------------------------------------- #
# Staging scoring
# --------------------------------------------------------------------------- #

# Tunables. Documented inline so the IC can audit how the score was built.
_W_INCIDENT = 0.30
_W_WATER = 0.25
_W_STATION = 0.20
_W_PAVED = 0.10
_W_ELEV = 0.15
_SAFE_STANDOFF_KM = 2.0


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


def score_staging_candidate(
    loc_latlon: tuple[float, float],
    paved_areas: list[dict[str, Any]],
    water: list[dict[str, Any]],
    fire_stations: list[dict[str, Any]],
    dem_elevation: float,
    incident_latlon: tuple[float, float],
) -> float:
    """Composite 0..1 staging-area score.

    Components (each in 0..1, then weighted):

    - **incident distance** — closer is better, but anything inside the
      2 km safe-standoff penalty band gets hard-capped to 0 on this axis.
      Sweet spot is roughly 3-8 km from the fire.
    - **water proximity** — nearest water feature within 5 km maps to 1.0.
    - **fire-station proximity** — nearest fire station within 10 km maps to 1.0.
    - **paved proximity** — nearest paved feature within 2 km maps to 1.0.
    - **elevation prominence** — used as a proxy for radio line-of-sight.
      Normalized against a 1500 m reference.
    """
    inc_km = _haversine_km(loc_latlon, incident_latlon)
    if inc_km < _SAFE_STANDOFF_KM:
        incident_score = 0.0
    else:
        # Tent function peaking at 5 km, falling off past 15 km.
        if inc_km <= 5.0:
            incident_score = 1.0 - (5.0 - inc_km) / (5.0 - _SAFE_STANDOFF_KM) * 0.3
        else:
            incident_score = max(0.0, 1.0 - (inc_km - 5.0) / 10.0)

    water_km = _nearest_km(loc_latlon, water)
    water_score = max(0.0, 1.0 - water_km / 5.0) if math.isfinite(water_km) else 0.0

    station_km = _nearest_km(loc_latlon, fire_stations)
    station_score = (
        max(0.0, 1.0 - station_km / 10.0) if math.isfinite(station_km) else 0.0
    )

    paved_km = _nearest_km(loc_latlon, paved_areas)
    paved_score = max(0.0, 1.0 - paved_km / 2.0) if math.isfinite(paved_km) else 0.0

    elev_score = max(0.0, min(1.0, float(dem_elevation) / 1500.0))

    return round(
        _W_INCIDENT * incident_score
        + _W_WATER * water_score
        + _W_STATION * station_score
        + _W_PAVED * paved_score
        + _W_ELEV * elev_score,
        4,
    )
