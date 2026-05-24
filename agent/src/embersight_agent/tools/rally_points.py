"""Rally-point / evacuation-destination discovery.

Pulls **defined** safe-haven destinations from three concurrent sources:

1. **OSM Overpass** — purpose-tagged places people actually muster:
   - ``emergency=assembly_point`` (the literal OSM rally-point tag)
   - ``social_facility=shelter`` + ``social_facility:for=evacuation``
   - schools / community centres / town halls / stadiums / sports centres /
     camp sites / fairgrounds
2. **HIFLD ArcGIS REST** — US-wide structured open data with capacity
   attributes (Public/Private Schools, Local Emergency Operations Centers).
   Endpoint URLs are overridable via env so a moved HIFLD service doesn't
   silently break the agent.
3. **CAL FIRE incidents JSON** — best-effort scrape of the per-incident
   evacuation-centre mentions, used as an enrichment layer; never a hard
   dependency.

Each source returns a list of normalized ``RallyPoint`` dicts. The agent
dedupes by proximity (preferring higher-priority types), scores them
(distance band + wind alignment + type weight + capacity), and routes
the incident to the top N via the existing OSMnx drive graph.

Every fetch is wrapped in try/except so any single source failing
degrades gracefully — the agent still produces routes from whatever
sources came back.
"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx

from .overpass import OVERPASS, USER_AGENT, query_osm

# --------------------------------------------------------------------------- #
# Type system
# --------------------------------------------------------------------------- #

# Higher number = higher priority as an evacuation destination.
# - assembly_point: the literal OSM tag for "rally here"
# - shelter:        social-services shelters (open or known)
# - eoc:            Emergency Operations Centers (HIFLD)
# - school:         primary evac-center default in CA (gyms, parking, water)
# - community_ctr:  town hall / community centre
# - fairground:     county fairgrounds — frequent county evac sites
# - stadium:        large open / parking
# - sports_centre:  rec centres with showers
# - camp_site:      remote rally for rural incidents
# - fire_station:   staging-only, not a civilian evac center
RALLY_TYPE_PRIORITY: dict[str, int] = {
    "assembly_point": 10,
    "shelter": 9,
    "eoc": 8,
    "school": 7,
    "community_ctr": 6,
    "fairground": 6,
    "stadium": 5,
    "sports_centre": 5,
    "camp_site": 4,
    "town_hall": 4,
    "fire_station": 3,
    "calfire_evac": 11,  # explicitly published by CAL FIRE for this incident
    "unknown": 1,
}

# Hospitals exist in HIFLD but should NOT surface as rally points —
# evacuating civilians to a hospital is dangerous (medical surge).
EXCLUDED_TYPES: frozenset[str] = frozenset({"hospital"})

# --------------------------------------------------------------------------- #
# OSM Overpass — rally points
# --------------------------------------------------------------------------- #


def _osm_tag_to_type(tags: dict[str, Any]) -> str:
    """Map an OSM feature's tags to one of our canonical rally types."""
    if tags.get("emergency") == "assembly_point":
        return "assembly_point"
    sf = tags.get("social_facility")
    if sf == "shelter":
        # Be more selective when 'for' is set — accept evacuation/disaster only.
        for_v = (tags.get("social_facility:for") or "").lower()
        if not for_v or "evacuation" in for_v or "disaster" in for_v or "homeless" in for_v:
            return "shelter"
    amenity = tags.get("amenity")
    if amenity == "school":
        return "school"
    if amenity == "community_centre":
        return "community_ctr"
    if amenity == "townhall":
        return "town_hall"
    if amenity == "fire_station":
        return "fire_station"
    leisure = tags.get("leisure")
    if leisure == "stadium":
        return "stadium"
    if leisure == "sports_centre":
        return "sports_centre"
    tourism = tags.get("tourism")
    if tourism == "camp_site":
        return "camp_site"
    return "unknown"


async def fetch_osm_rally_points(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Pull every OSM tag we treat as an evacuation destination."""
    queries: list[tuple[str, dict[str, list[str]], tuple[str, ...]]] = [
        (
            "assembly_point",
            {"emergency": ["assembly_point"]},
            ("node", "way", "relation"),
        ),
        ("shelter", {"social_facility": ["shelter"]}, ("node", "way", "relation")),
        (
            "school+community+townhall+firestation",
            {"amenity": ["school", "community_centre", "townhall", "fire_station"]},
            ("node", "way", "relation"),
        ),
        ("stadium+sports", {"leisure": ["stadium", "sports_centre"]}, ("way", "relation")),
        ("camp_site", {"tourism": ["camp_site"]}, ("node", "way", "relation")),
    ]
    elements: list[dict[str, Any]] = []
    for _label, filters, etypes in queries:
        try:
            chunk = await query_osm(bbox, filters, element_types=etypes)
        except Exception:
            continue
        elements.extend(chunk)
    return _normalize_osm_rally(elements)


def _normalize_osm_rally(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags") or {}
        rtype = _osm_tag_to_type(tags)
        if rtype == "unknown":
            continue
        lat, lon = _feature_latlon(el)
        if lat is None or lon is None:
            continue
        name = (
            tags.get("name")
            or tags.get("operator")
            or tags.get("ref")
            or f"{rtype}_{el.get('id', 'osm')}"
        )
        out.append(
            {
                "id": f"osm:{el.get('type', '?')}:{el.get('id', '?')}",
                "name": str(name),
                "loc": (float(lat), float(lon)),
                "rally_type": rtype,
                "capacity": _osm_capacity_proxy(tags),
                "source": "osm",
                "raw_tags": tags,
            }
        )
    return out


def _osm_capacity_proxy(tags: dict[str, Any]) -> int | None:
    """Best-effort capacity from common OSM tags. None when unknown."""
    for key in ("capacity", "capacity:persons"):
        v = tags.get(key)
        if v is None:
            continue
        try:
            return int(float(str(v).split()[0]))
        except (TypeError, ValueError):
            continue
    return None


def _feature_latlon(feature: dict[str, Any]) -> tuple[float | None, float | None]:
    if "lat" in feature and "lon" in feature:
        return float(feature["lat"]), float(feature["lon"])
    c = feature.get("center")
    if isinstance(c, dict) and "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    return None, None


# --------------------------------------------------------------------------- #
# HIFLD ArcGIS — rally points
# --------------------------------------------------------------------------- #

HIFLD_TIMEOUT_S = 15.0

# Default HIFLD endpoints. Overridable via env so a moved service can be
# patched without a redeploy. Each URL must be an ArcGIS FeatureServer
# layer that supports `f=geojson` + bbox query.
HIFLD_PUBLIC_SCHOOLS_URL = os.environ.get(
    "HIFLD_PUBLIC_SCHOOLS_URL",
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Public_Schools/FeatureServer/0/query",
)
HIFLD_PRIVATE_SCHOOLS_URL = os.environ.get(
    "HIFLD_PRIVATE_SCHOOLS_URL",
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Private_Schools/FeatureServer/0/query",
)
HIFLD_EOC_URL = os.environ.get(
    "HIFLD_EOC_URL",
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Local_Emergency_Operations_Centers_EOC/FeatureServer/0/query",
)


async def _hifld_query(
    url: str,
    bbox: tuple[float, float, float, float],
    rally_type: str,
    name_field_candidates: tuple[str, ...],
    capacity_field_candidates: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Query a HIFLD ArcGIS FeatureServer layer over a bbox, normalize.

    bbox is (S, W, N, E); ArcGIS envelope wants xmin,ymin,xmax,ymax.
    """
    s, w, n, e = bbox
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": "200",
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=HIFLD_TIMEOUT_S, headers=headers) as cli:
            r = await cli.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    features = data.get("features") or []
    for f in features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") != "Point" or not isinstance(coords, list) or len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        props = f.get("properties") or {}
        name = _first_field(props, name_field_candidates) or f"{rally_type}_{f.get('id', 'hifld')}"
        capacity = None
        if capacity_field_candidates:
            cap_raw = _first_field(props, capacity_field_candidates)
            try:
                if cap_raw is not None:
                    capacity = int(float(cap_raw))
                    if capacity <= 0:
                        capacity = None
            except (TypeError, ValueError):
                capacity = None
        out.append(
            {
                "id": f"hifld:{rally_type}:{props.get('OBJECTID', props.get('id', '?'))}",
                "name": str(name),
                "loc": (lat, lon),
                "rally_type": rally_type,
                "capacity": capacity,
                "source": "hifld",
                "raw_tags": props,
            }
        )
    return out


def _first_field(props: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    """Return the first present, non-empty value from candidates (case-insensitive)."""
    lowered = {str(k).lower(): k for k in props.keys()}
    for c in candidates:
        key = lowered.get(c.lower())
        if key is None:
            continue
        v = props.get(key)
        if v not in (None, "", "NULL", "Null"):
            return v
    return None


async def fetch_hifld_rally_points(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Fetch HIFLD schools + EOCs over the bbox in parallel."""
    import asyncio

    schools_pub_t, schools_priv_t, eoc_t = await asyncio.gather(
        _hifld_query(
            HIFLD_PUBLIC_SCHOOLS_URL,
            bbox,
            "school",
            name_field_candidates=("NAME", "SCH_NAME", "SCHOOL"),
            capacity_field_candidates=("ENROLLMENT", "POPULATION"),
        ),
        _hifld_query(
            HIFLD_PRIVATE_SCHOOLS_URL,
            bbox,
            "school",
            name_field_candidates=("NAME", "SCH_NAME", "SCHOOL"),
            capacity_field_candidates=("ENROLLMENT", "POPULATION"),
        ),
        _hifld_query(
            HIFLD_EOC_URL,
            bbox,
            "eoc",
            name_field_candidates=("NAME", "EOC_NAME", "FACILITY"),
        ),
        return_exceptions=True,
    )

    def _ok(x: Any) -> list[dict[str, Any]]:
        return x if isinstance(x, list) else []

    return _ok(schools_pub_t) + _ok(schools_priv_t) + _ok(eoc_t)


# --------------------------------------------------------------------------- #
# CAL FIRE — best-effort per-incident evacuation centers
# --------------------------------------------------------------------------- #

CALFIRE_LIST_URL = os.environ.get(
    "CALFIRE_INCIDENTS_URL",
    "https://incidents.fire.ca.gov/umbraco/Api/IncidentApi/GetIncidents",
)


async def fetch_calfire_evac_centers(
    incident_name: str | None,
    incident_latlon: tuple[float, float],
    radius_km: float = 50.0,
) -> list[dict[str, Any]]:
    """Best-effort scrape: pull CAL FIRE's current incident list and look
    for evacuation-center entries on the matched incident.

    The CAL FIRE incident JSON schema isn't a public contract — fields
    have shifted historically. We probe a small set of likely shapes and
    silently return ``[]`` on any failure so the agent never hard-depends
    on this source. The other two backbones (OSM + HIFLD) carry the
    feature on their own.
    """
    if not incident_name:
        return []
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as cli:
            r = await cli.get(CALFIRE_LIST_URL)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []

    incidents = data if isinstance(data, list) else data.get("Incidents") or []
    matched: dict[str, Any] | None = None
    target = incident_name.strip().lower()
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        name = (inc.get("Name") or inc.get("name") or "").strip().lower()
        if not name:
            continue
        if name == target or target in name or name in target:
            matched = inc
            break
    if matched is None:
        return []

    # CAL FIRE has historically exposed evac-related fields under various
    # names. Probe a list of common ones, accept structured items only.
    out: list[dict[str, Any]] = []
    for key in (
        "EvacuationCenters",
        "Evacuation",
        "EvacShelters",
        "Shelters",
    ):
        items = matched.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            lat = it.get("Latitude") or it.get("lat")
            lon = it.get("Longitude") or it.get("lon")
            if lat is None or lon is None:
                continue
            try:
                lat_f, lon_f = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            name = it.get("Name") or it.get("Location") or "CAL FIRE evac center"
            out.append(
                {
                    "id": f"calfire:{matched.get('UniqueId', '?')}:{it.get('Id', name)}",
                    "name": str(name),
                    "loc": (lat_f, lon_f),
                    "rally_type": "calfire_evac",
                    "capacity": None,
                    "source": "calfire",
                    "raw_tags": it,
                }
            )
    # Drop anything outside the AOI — CAL FIRE sometimes lists county-wide.
    return [
        p for p in out if _haversine_km(p["loc"], incident_latlon) <= radius_km
    ]


# --------------------------------------------------------------------------- #
# Dedup + scoring
# --------------------------------------------------------------------------- #

_DEDUP_RADIUS_KM = 0.25


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


def dedup_rally_points(
    points: list[dict[str, Any]], radius_km: float = _DEDUP_RADIUS_KM
) -> list[dict[str, Any]]:
    """Greedy dedup. Sort by type priority first so the higher-priority
    point survives when two sources report the same school."""
    sorted_pts = sorted(
        points,
        key=lambda p: RALLY_TYPE_PRIORITY.get(p.get("rally_type", "unknown"), 0),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    for p in sorted_pts:
        loc = p.get("loc")
        if not loc:
            continue
        if any(_haversine_km(loc, k["loc"]) < radius_km for k in kept):
            continue
        kept.append(p)
    return kept


# Scoring tunables — every axis is 0..1.
_DIST_MIN_KM = 5.0
_DIST_MAX_KM = 30.0
_DIST_SWEET_KM = 12.0
_TYPE_NORM = 11.0  # divide by RALLY_TYPE_PRIORITY's max
_CAPACITY_SOFT = 500.0  # capacity that maps to 1.0

# Component weights for the rally-point composite.
_W_TYPE = 0.30
_W_DISTANCE = 0.25
_W_WIND = 0.25
_W_CAPACITY = 0.10
_W_SOURCE = 0.10  # multi-source corroboration nudge


def _distance_score(dist_km: float) -> float:
    """Tent: 0 below MIN, peak at SWEET, 0 above MAX."""
    if dist_km < _DIST_MIN_KM or dist_km > _DIST_MAX_KM:
        return 0.0
    if dist_km <= _DIST_SWEET_KM:
        span = _DIST_SWEET_KM - _DIST_MIN_KM
        return (dist_km - _DIST_MIN_KM) / span if span > 0 else 1.0
    span = _DIST_MAX_KM - _DIST_SWEET_KM
    return max(0.0, 1.0 - (dist_km - _DIST_SWEET_KM) / span)


def _wind_score(
    point: tuple[float, float],
    incident: tuple[float, float],
    wind_from_deg: float | None,
) -> float:
    """Upwind point = 1.0, crosswind = 0.5, downwind = 0.0. Neutral 0.5
    when wind unknown so wind-blindness doesn't crash type-strong points."""
    if wind_from_deg is None or not math.isfinite(wind_from_deg):
        return 0.5
    bearing = _bearing_deg(incident, point)
    offset = _ang_diff_deg(bearing, wind_from_deg)
    return max(0.0, (1.0 + math.cos(math.radians(offset))) / 2.0)


def wind_relation_tag(
    point: tuple[float, float],
    incident: tuple[float, float],
    wind_from_deg: float | None,
) -> str:
    if wind_from_deg is None or not math.isfinite(wind_from_deg):
        return "unknown"
    bearing = _bearing_deg(incident, point)
    offset = _ang_diff_deg(bearing, wind_from_deg)
    if offset <= 45.0:
        return "upwind"
    if offset <= 135.0:
        return "crosswind"
    return "downwind"


def score_rally_point(
    point: dict[str, Any],
    incident_latlon: tuple[float, float],
    wind_from_deg: float | None,
) -> dict[str, Any]:
    """Composite 0..1 score + per-component breakdown."""
    loc = point["loc"]
    dist_km = _haversine_km(loc, incident_latlon)
    rtype = point.get("rally_type", "unknown")
    type_score = RALLY_TYPE_PRIORITY.get(rtype, 1) / _TYPE_NORM
    dist_score = _distance_score(dist_km)
    wind_s = _wind_score(loc, incident_latlon, wind_from_deg)
    cap = point.get("capacity")
    if cap and cap > 0:
        cap_score = min(1.0, math.log10(1 + cap) / math.log10(1 + _CAPACITY_SOFT))
    else:
        cap_score = 0.3  # unknown capacity isn't a hard zero
    # Multi-source corroboration: a point seen by both OSM and HIFLD
    # gets a small bump. We approximate this by giving HIFLD a slight
    # bonus (structured data) and CAL FIRE a bigger bonus (incident-specific).
    src = point.get("source", "osm")
    source_score = {"calfire": 1.0, "hifld": 0.7, "osm": 0.5}.get(src, 0.5)

    total = (
        _W_TYPE * type_score
        + _W_DISTANCE * dist_score
        + _W_WIND * wind_s
        + _W_CAPACITY * cap_score
        + _W_SOURCE * source_score
    )
    return {
        "total": round(total, 4),
        "components": {
            "type": round(type_score, 3),
            "distance": round(dist_score, 3),
            "wind": round(wind_s, 3),
            "capacity": round(cap_score, 3),
            "source": round(source_score, 3),
        },
        "weights": {
            "type": _W_TYPE,
            "distance": _W_DISTANCE,
            "wind": _W_WIND,
            "capacity": _W_CAPACITY,
            "source": _W_SOURCE,
        },
        "raw": {
            "distance_km": round(dist_km, 2),
            "rally_type": rtype,
            "capacity": cap,
            "wind_from_deg": wind_from_deg,
        },
    }


def rank_rally_points(
    points: list[dict[str, Any]],
    incident_latlon: tuple[float, float],
    wind_from_deg: float | None,
    *,
    aoi_radius_km: float = 30.0,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """Filter, dedup, score, sort. Returns top_n with score attached."""
    in_aoi = [
        p
        for p in points
        if p.get("loc") and _haversine_km(p["loc"], incident_latlon) <= aoi_radius_km
        and p.get("rally_type") not in EXCLUDED_TYPES
    ]
    deduped = dedup_rally_points(in_aoi)
    scored: list[dict[str, Any]] = []
    for p in deduped:
        s = score_rally_point(p, incident_latlon, wind_from_deg)
        scored.append(
            {
                **p,
                "score": s["total"],
                "score_components": s["components"],
                "score_weights": s["weights"],
                "score_raw": s["raw"],
                "wind_relation": wind_relation_tag(
                    p["loc"], incident_latlon, wind_from_deg
                ),
            }
        )
    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored[:top_n]


# --------------------------------------------------------------------------- #
# Public entry: discover everything in one call
# --------------------------------------------------------------------------- #


async def discover_rally_points(
    bbox: tuple[float, float, float, float],
    incident_latlon: tuple[float, float],
    incident_name: str | None,
    wind_from_deg: float | None,
    *,
    aoi_radius_km: float = 30.0,
    top_n: int = 8,
) -> dict[str, Any]:
    """Fan out to all three sources concurrently, then rank.

    Returns:
        {
            "points": [...],          # top_n ranked rally points
            "counts": {osm, hifld, calfire, total_raw, after_dedup},
            "source_failures": [...], # sources that errored (informational)
        }
    """
    import asyncio

    osm_t, hifld_t, calfire_t = await asyncio.gather(
        fetch_osm_rally_points(bbox),
        fetch_hifld_rally_points(bbox),
        fetch_calfire_evac_centers(incident_name, incident_latlon),
        return_exceptions=True,
    )

    def _ok(x: Any) -> list[dict[str, Any]]:
        return x if isinstance(x, list) else []

    osm = _ok(osm_t)
    hifld = _ok(hifld_t)
    calfire = _ok(calfire_t)
    source_failures = [
        name
        for name, val in (("osm", osm_t), ("hifld", hifld_t), ("calfire", calfire_t))
        if isinstance(val, BaseException)
    ]
    raw = osm + hifld + calfire
    ranked = rank_rally_points(
        raw,
        incident_latlon,
        wind_from_deg,
        aoi_radius_km=aoi_radius_km,
        top_n=top_n,
    )
    return {
        "points": ranked,
        "counts": {
            "osm": len(osm),
            "hifld": len(hifld),
            "calfire": len(calfire),
            "total_raw": len(raw),
            "after_dedup_ranked": len(ranked),
        },
        "source_failures": source_failures,
        "citations": [
            {"name": "OSM Overpass", "url": OVERPASS},
            {"name": "HIFLD Public Schools", "url": HIFLD_PUBLIC_SCHOOLS_URL},
            {"name": "HIFLD Private Schools", "url": HIFLD_PRIVATE_SCHOOLS_URL},
            {"name": "HIFLD Local EOCs", "url": HIFLD_EOC_URL},
            {"name": "CAL FIRE incidents", "url": CALFIRE_LIST_URL},
        ],
    }
