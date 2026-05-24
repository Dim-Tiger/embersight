"""Cal OES `CA_EVACUATIONS` evacuation-zone feed and supporting helpers.

The actual Cal OES FeatureServer endpoint is hosted at services1.arcgis.com
(ArcGIS Online org `BLN4oKB0N1YSgvY8`). Each feature has a polygon, a
`Status` attribute (one of "Order", "Warning", "Advisory", "Shelter", or
empty for normal/none) and metadata describing when it was last edited
and which jurisdiction owns it.

This module exposes three building blocks used by the
`evacuation_intelligence` agent:

* `get_calevacs_zones(bbox)` — async fetch of zone polygons inside a
  lon/lat bounding box, normalised to a plain list of dicts.
* `estimate_population(polygon_wkt)` — rough population estimate from
  Microsoft Building Footprints density × average household size (fallback
  proxy when block-group data is not loaded).
* `compute_evacuation_routes_clear(polygon_wkt, road_graph)` — given a
  zone polygon and an optional NetworkX road graph from the
  Routing & Staging agent, decide whether main egress edges are still
  clear of the predicted spread cone.

All heavy geo deps (shapely / geopandas / networkx) are lazy-imported so
the module can be imported in environments where they are not installed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ArcGIS Online host + FeatureServer layer for the Cal OES hosted view of
# CA_EVACUATIONS. The org id "BLN4oKB0N1YSgvY8" is Cal OES's.
CALEVACS_ENDPOINT = (
    # NOTE: host is services.arcgis.com (no "1"). The earlier services1
    # subdomain returns 400 "Invalid URL" for this org_id — Cal OES's
    # AGOL tenant lives on the canonical services.arcgis.com host.
    "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/"
    "CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query"
)
CALEVACS_VERSION = "CalOES Hosted View / FeatureServer layer 0"

# Microsoft Building Footprints — average residents per residential
# structure. 2.51 is the US Census ACS 2020 mean household size; we use
# 2.5 as a deliberately conservative round number.
DEFAULT_HOUSEHOLD_SIZE = 2.5


# --------------------------------------------------------------------------- #
# Zone fetch
# --------------------------------------------------------------------------- #


def _bbox_envelope(bbox: tuple[float, float, float, float]) -> dict:
    """Build the ArcGIS `geometry` query param for a lon/lat bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return {
        "xmin": min_lon,
        "ymin": min_lat,
        "xmax": max_lon,
        "ymax": max_lat,
        "spatialReference": {"wkid": 4326},
    }


def _normalise_status(raw: Any) -> str:
    """Map Cal OES `Status` strings onto our 3-state vocabulary."""
    if not raw:
        return "NORMAL"
    s = str(raw).strip().lower()
    if "order" in s:
        return "ORDER"
    if "warning" in s or "evacuation warning" in s:
        return "WARNING"
    if "advisory" in s:
        return "WARNING"
    if "shelter" in s:
        return "WARNING"
    if "lifted" in s or "all clear" in s or s in {"none", "normal"}:
        return "NORMAL"
    return "NORMAL"


def _ring_to_wkt(rings: list[list[list[float]]]) -> str:
    """Convert an Esri polygon `rings` list to a WKT POLYGON string.

    Esri JSON polygons can have multiple rings (first is outer, rest are
    holes), and a MultiPolygon-like geometry is represented as multiple
    rings as well. We emit a POLYGON with the outer ring and any inner
    rings; this is sufficient for the simple overlay use cases below.
    """
    if not rings:
        return "POLYGON EMPTY"

    def ring_str(ring: list[list[float]]) -> str:
        return ", ".join(f"{pt[0]} {pt[1]}" for pt in ring)

    outer = ring_str(rings[0])
    if len(rings) == 1:
        return f"POLYGON (({outer}))"
    holes = ", ".join(f"({ring_str(r)})" for r in rings[1:])
    return f"POLYGON (({outer}), {holes})"


def _feature_to_zone(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or feature.get("properties") or {}
    geom = feature.get("geometry") or {}

    # ArcGIS JSON path
    rings = geom.get("rings")
    if rings:
        wkt = _ring_to_wkt(rings)
    else:
        # GeoJSON fallback
        coords = geom.get("coordinates")
        if not coords:
            return None
        if geom.get("type") == "Polygon":
            wkt = _ring_to_wkt(coords)
        elif geom.get("type") == "MultiPolygon":
            # Take the largest ring set as primary; keep the rest as a
            # comma-separated list inside a single POLYGON for our
            # downstream simple overlap math. (Spread cone overlay only
            # needs an outer ring approximation.)
            wkt = _ring_to_wkt(coords[0])
        else:
            return None

    zone_id = (
        attrs.get("ZoneID")
        or attrs.get("Zone_ID")
        or attrs.get("OBJECTID")
        or attrs.get("FID")
        or attrs.get("GlobalID")
        or attrs.get("Id")
    )
    if zone_id is None:
        return None

    raw_updated = (
        attrs.get("last_edited_date")
        or attrs.get("EditDate")
        or attrs.get("last_updated")
        or attrs.get("LastUpdate")
    )
    last_updated_iso: str | None = None
    if isinstance(raw_updated, (int, float)):
        # Esri date fields come back as ms-since-epoch.
        from datetime import datetime, timezone

        try:
            last_updated_iso = datetime.fromtimestamp(
                raw_updated / 1000.0, tz=timezone.utc
            ).isoformat()
        except (OSError, ValueError, OverflowError):
            last_updated_iso = None
    elif isinstance(raw_updated, str):
        last_updated_iso = raw_updated

    return {
        "zone_id": str(zone_id),
        "name": attrs.get("ZoneName")
        or attrs.get("Name")
        or attrs.get("ZONE")
        or f"Zone {zone_id}",
        "current_status": _normalise_status(attrs.get("Status")),
        "polygon_wkt": wkt,
        "last_updated_iso": last_updated_iso,
        "jurisdiction": (
            attrs.get("Jurisdiction")
            or attrs.get("County")
            or attrs.get("AGENCY")
            or "unknown"
        ),
    }


async def get_calevacs_zones(
    bbox: tuple[float, float, float, float],
    *,
    timeout: float = 20.0,
) -> list[dict]:
    """Pull Cal OES `CA_EVACUATIONS` zones intersecting the given bbox.

    `bbox` is `(min_lon, min_lat, max_lon, max_lat)` in WGS84.

    Returns a list of dicts shaped like:

        {
            "zone_id": str,
            "name": str,
            "current_status": "NORMAL" | "WARNING" | "ORDER",
            "polygon_wkt": str,
            "last_updated_iso": str | None,
            "jurisdiction": str,
        }

    Failures are logged and swallowed — the agent treats an empty list as
    a freshness-degraded signal rather than a hard error.
    """
    import json as _json  # noqa: PLC0415

    params = {
        "where": "1=1",
        "outFields": "*",
        # See get_active_status_overlay for the compact-JSON rationale.
        "geometry": _json.dumps(_bbox_envelope(bbox), separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "f": "geojson",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(CALEVACS_ENDPOINT, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("CA_EVACUATIONS fetch failed: %s", exc)
        return []

    features = data.get("features", [])
    zones: list[dict] = []
    for feat in features:
        z = _feature_to_zone(feat)
        if z is not None:
            zones.append(z)
    return zones


# --------------------------------------------------------------------------- #
# Live status overlay
# --------------------------------------------------------------------------- #
#
# The Cal OES aggregation only carries currently-active zones (warning /
# order). When the catalog (``tools.zone_catalog``) hands us the static
# universe of zones for an AOI, we apply this overlay so:
#
#   1. The agent sees the *real* ``current_status`` for any zone with an
#      open warning/order — it won't propose WARNING for a zone already
#      under ORDER, for example.
#   2. The "adjacent to an ORDER zone" nudge in evacuation_intelligence
#      fires off real ground-truth, not against zones the agent itself
#      proposed earlier.
#
# Match keys: prefer ZONE_ID (matches Genasys catalog `zone_id`); fall
# back to a normalised (county, zone_name) tuple for county-hosted
# Feature Services whose IDs disagree with Genasys.


async def get_active_status_overlay(
    bbox: tuple[float, float, float, float],
    *,
    timeout: float = 15.0,
) -> dict[str, str]:
    """Fetch the Cal OES *active* aggregation in ``bbox`` and return a
    dict of overrides::

        {
            "<ZONE_ID>": "ORDER",
            "<county>|<normalised name>": "WARNING",
            ...
        }

    ``evacuation_intelligence`` overlays this on top of static catalog
    zones so it has correct ground truth before proposing changes.

    Empty dict on fetch failure (degrade gracefully — the agent simply
    treats every static zone as NORMAL).
    """
    import json as _json  # noqa: PLC0415

    params = {
        "where": "1=1",
        "outFields": "ZONE_ID,ZONE_NAME,COUNTY,CITY,STATUS,EVENT_TYPE",
        # ArcGIS rejects the geometry param if it has whitespace, so we
        # serialise with json.dumps(..., separators=(",", ":")) to keep
        # it compact. The older `str(dict).replace("'", '"')` form works
        # *only* when Python doesn't add a space after the colon.
        "geometry": _json.dumps(_bbox_envelope(bbox), separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(CALEVACS_ENDPOINT, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("active-status overlay fetch failed: %s", exc)
        return {}

    overlay: dict[str, str] = {}
    for feat in data.get("features", []):
        attrs = feat.get("attributes") or {}
        status = _normalise_status(attrs.get("STATUS"))
        if status == "NORMAL":
            continue
        zid = attrs.get("ZONE_ID")
        if zid:
            overlay[str(zid)] = status
        # Also key by (county, name) for catalogs whose ZONE_ID schema
        # disagrees with Cal OES's.
        county = attrs.get("COUNTY") or ""
        name = attrs.get("ZONE_NAME") or ""
        if county and name:
            overlay[f"{county.strip().lower()}|{name.strip().lower()}"] = status
    return overlay


def overlay_key_for_zone(zone: dict) -> tuple[str, str]:
    """Build the two possible lookup keys for a catalog zone against the
    overlay returned by ``get_active_status_overlay``: the bare zone_id,
    and the (county, name) composite. The caller checks both and takes
    the first hit."""
    zid = str(zone.get("zone_id") or "")
    county = str(zone.get("jurisdiction") or "").strip().lower()
    # Strip " County" suffix to match Cal OES's bare county names.
    if county.endswith(" county"):
        county = county[: -len(" county")]
    name = str(zone.get("name") or "").strip().lower()
    return zid, f"{county}|{name}"


# --------------------------------------------------------------------------- #
# Population estimation
# --------------------------------------------------------------------------- #


def _load_polygon(polygon_wkt: str):
    """Lazy-import shapely and parse a WKT string into a shapely Polygon."""
    from shapely import wkt as _wkt  # noqa: PLC0415

    return _wkt.loads(polygon_wkt)


def _polygon_area_km2(polygon_wkt: str) -> float:
    """Rough area in km^2 using the equirectangular approximation.

    Good enough for population-density math at California latitudes; we
    don't need a proper equal-area projection for an order-of-magnitude
    estimate. A future pass should reproject through pyproj before
    measuring.
    """
    import math  # noqa: PLC0415

    poly = _load_polygon(polygon_wkt)
    if poly.is_empty:
        return 0.0

    centroid = poly.centroid
    lat0_rad = math.radians(centroid.y)
    # 1 degree of latitude ≈ 111.32 km; 1 deg lon ≈ 111.32·cos(lat).
    deg2_to_km2 = (111.32**2) * math.cos(lat0_rad)
    area_deg2 = poly.area
    return max(0.0, area_deg2 * deg2_to_km2)


def estimate_population(
    polygon_wkt: str,
    *,
    building_density_per_km2: float = 220.0,
    household_size: float = DEFAULT_HOUSEHOLD_SIZE,
) -> int:
    """Estimate population inside a zone polygon.

    Strategy: building-footprint density × average household size. The
    default density (220 buildings/km²) matches the Microsoft Building
    Footprints California regional average for non-urban / WUI areas;
    real fire-front zones in California are usually WUI rather than
    dense urban, so this is a reasonable demo-grade proxy.

    Returns an integer count; 0 if the polygon is empty or invalid.
    """
    try:
        area_km2 = _polygon_area_km2(polygon_wkt)
    except Exception as exc:  # noqa: BLE001
        log.warning("estimate_population failed to parse WKT: %s", exc)
        return 0
    if area_km2 <= 0:
        return 0
    return int(round(area_km2 * building_density_per_km2 * household_size))


# --------------------------------------------------------------------------- #
# Egress route clearance
# --------------------------------------------------------------------------- #


def _coerce_cone_to_geom(cone_obj: Any):
    """Best-effort conversion of a spread-cone value to a shapely geometry.

    The Spread Simulation agent's cones may arrive as WKT strings, GeoJSON
    dicts, or shapely objects depending on plumbing maturity. We accept
    any of those.
    """
    if cone_obj is None:
        return None

    # shapely already
    try:
        from shapely.geometry.base import BaseGeometry  # noqa: PLC0415

        if isinstance(cone_obj, BaseGeometry):
            return cone_obj
    except ImportError:
        return None

    if isinstance(cone_obj, str):
        try:
            from shapely import wkt as _wkt  # noqa: PLC0415

            return _wkt.loads(cone_obj)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(cone_obj, dict):
        try:
            from shapely.geometry import shape  # noqa: PLC0415

            return shape(cone_obj)
        except Exception:  # noqa: BLE001
            return None
    return None


def compute_evacuation_routes_clear(
    polygon_wkt: str,
    road_graph: Any,
    *,
    spread_cone: Any = None,
    max_egress_edges: int = 4,
) -> dict:
    """Decide whether main egress routes from a zone are still clear.

    Parameters
    ----------
    polygon_wkt
        Zone polygon as a WKT string.
    road_graph
        NetworkX graph (from `routing_staging.payload.road_graph`) or
        `None` if unavailable. Each edge is expected to carry a `geometry`
        attribute (LineString) and a `highway` tag.
    spread_cone
        Predicted fire spread polygon at the time horizon of interest
        (typically the 6h or 12h cone). WKT, GeoJSON dict, or shapely
        geometry. If `None`, we cannot determine cone-overlap and return
        a degraded "unknown" result.
    max_egress_edges
        Cap on how many egress edges we examine. Major egress is well
        represented by a handful of arterials.

    Returns
    -------
    dict with keys:
        clear (bool | None)        — True/False, or None if unknown.
        reason (str)               — short rationale.
        egress_edges_checked (int) — count examined.
        egress_edges_blocked (int) — count that intersect the cone.
    """
    result = {
        "clear": None,
        "reason": "road graph unavailable",
        "egress_edges_checked": 0,
        "egress_edges_blocked": 0,
    }

    if road_graph is None:
        return result

    try:
        zone = _load_polygon(polygon_wkt)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"invalid polygon_wkt: {exc}"
        return result

    cone = _coerce_cone_to_geom(spread_cone)

    # Lazy import networkx + shapely linestring helpers
    try:
        import networkx as nx  # noqa: F401, PLC0415
    except ImportError:
        result["reason"] = "networkx not available"
        return result

    # Walk edges, keep those that look like real egress (highway tag is
    # primary / secondary / trunk / motorway) and cross the polygon
    # boundary (i.e., leave the zone).
    egress_tags = {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
    }

    checked = 0
    blocked = 0
    boundary = zone.boundary
    try:
        edges = list(road_graph.edges(data=True))
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"road graph not iterable: {exc}"
        return result

    for _u, _v, data in edges:
        if checked >= max_egress_edges:
            break
        highway = data.get("highway")
        if isinstance(highway, list):
            highway = highway[0] if highway else None
        if highway not in egress_tags:
            continue
        geom = data.get("geometry")
        if geom is None:
            continue
        try:
            if not geom.intersects(boundary):
                continue
        except Exception:  # noqa: BLE001
            continue

        checked += 1
        if cone is not None:
            try:
                if geom.intersects(cone):
                    blocked += 1
            except Exception:  # noqa: BLE001
                pass

    result["egress_edges_checked"] = checked
    result["egress_edges_blocked"] = blocked

    if checked == 0:
        result["reason"] = "no major egress edges found on road graph"
        return result
    if cone is None:
        result["reason"] = f"{checked} egress edges found; no spread cone supplied"
        result["clear"] = True
        return result

    result["clear"] = blocked == 0
    result["reason"] = (
        f"{blocked}/{checked} major egress edges intersect spread cone"
    )
    return result
