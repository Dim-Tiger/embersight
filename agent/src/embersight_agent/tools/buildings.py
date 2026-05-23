"""Microsoft Building Footprints + USA Structures spatial join.

Pass-2: spatial-join against the spread cone polygon. We use the FEMA
USA Structures FeatureServer as the primary source (it bundles MS
Building Footprints + USGS GMTED attribution + occupancy class), with
the MS Building Footprints Planetary Computer STAC as an aspirational
fallback. For unit-test friendliness all heavy geo dependencies
(geopandas/shapely) are lazy-imported inside the call sites.
"""

from __future__ import annotations

from typing import Any

import httpx

# FEMA USA Structures FeatureServer (Layer 0 = points; in production we'd
# also hit the polygons layer but the count-by-centroid suffices here).
USA_STRUCTURES_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)

# Microsoft Building Footprints — Planetary Computer STAC root. The
# pass-2 implementation walks the per-quadkey GeoJSONL tiles; we keep the
# URL here as documentation for downstream readers.
MS_BUILDINGS_STAC = (
    "https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
    "ms-buildings"
)


def _bbox_from_wkt(polygon_wkt: str) -> tuple[float, float, float, float]:
    """Lazy-shapely WKT -> (minx, miny, maxx, maxy)."""
    from shapely import wkt  # noqa: PLC0415

    geom = wkt.loads(polygon_wkt)
    minx, miny, maxx, maxy = geom.bounds
    return float(minx), float(miny), float(maxx), float(maxy)


def _arcgis_envelope(bbox: tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bbox
    return (
        f'{{"xmin":{minx},"ymin":{miny},"xmax":{maxx},"ymax":{maxy},'
        '"spatialReference":{"wkid":4326}}'
    )


def query_ms_buildings(polygon_wkt: str) -> dict[str, Any]:
    """Count MS Building Footprints intersecting the polygon.

    We proxy through the FEMA USA Structures FeatureServer (which embeds
    MS Building Footprints + USGS attribution) for a single
    HTTP-friendly endpoint. Returns:
      {count, sample_centroids: [(lon, lat), ...], total_footprint_sqm}
    """
    try:
        bbox = _bbox_from_wkt(polygon_wkt)
    except Exception as exc:  # noqa: BLE001
        return {
            "count": 0,
            "sample_centroids": [],
            "total_footprint_sqm": 0.0,
            "error": f"wkt-parse:{exc}",
        }

    envelope = _arcgis_envelope(bbox)
    params = {
        "where": "1=1",
        "geometry": envelope,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,HEIGHT,SQMETERS,OCC_CLS",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "2000",
        "f": "geojson",
    }
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(USA_STRUCTURES_URL, params=params)
            r.raise_for_status()
            data = r.json()
        if isinstance(data, dict) and "error" in data and "features" not in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"arcgis:{msg}")
    except Exception as exc:  # noqa: BLE001
        return {
            "count": 0,
            "sample_centroids": [],
            "total_footprint_sqm": 0.0,
            "error": f"http:{exc}",
        }

    features = data.get("features", []) or []
    total_sqm = 0.0
    sample: list[tuple[float, float]] = []
    for feat in features:
        props = feat.get("properties", {}) or {}
        sqm = props.get("SQMETERS")
        if isinstance(sqm, (int, float)):
            total_sqm += float(sqm)
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if coords and len(sample) < 25:
            try:
                if geom.get("type") == "Point":
                    sample.append((float(coords[0]), float(coords[1])))
                else:
                    # First ring, first vertex as a cheap centroid proxy.
                    ring = coords[0] if isinstance(coords[0], list) else coords
                    if isinstance(ring[0], list):
                        ring = ring[0]
                    sample.append((float(ring[0]), float(ring[1])))
            except Exception:  # noqa: BLE001
                pass

    return {
        "count": len(features),
        "sample_centroids": sample,
        "total_footprint_sqm": round(total_sqm, 1),
    }


def query_usa_structures(polygon_wkt: str) -> dict[str, Any]:
    """USA Structures occupancy-class breakdown for the polygon.

    Returns:
      {
        total: int,
        by_occupancy: {Residential: N, Commercial: N, Public: N, Industrial: N, ...},
        error?: str,
      }
    """
    try:
        bbox = _bbox_from_wkt(polygon_wkt)
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "by_occupancy": {}, "error": f"wkt-parse:{exc}"}

    envelope = _arcgis_envelope(bbox)
    params = {
        "where": "OCC_CLS IS NOT NULL",
        "geometry": envelope,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OCC_CLS",
        "groupByFieldsForStatistics": "OCC_CLS",
        "outStatistics": (
            '[{"statisticType":"count","onStatisticField":"OCC_CLS",'
            '"outStatisticFieldName":"n"}]'
        ),
        "f": "json",
    }

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(USA_STRUCTURES_URL, params=params)
            r.raise_for_status()
            data = r.json()
        if isinstance(data, dict) and "error" in data and "features" not in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"arcgis:{msg}")
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "by_occupancy": {}, "error": f"http:{exc}"}

    by_occ: dict[str, int] = {}
    for row in data.get("features", []) or []:
        attrs = row.get("attributes", {}) or {}
        cls = attrs.get("OCC_CLS") or "Unknown"
        n = int(attrs.get("n", 0) or 0)
        by_occ[cls] = by_occ.get(cls, 0) + n
    total = sum(by_occ.values())
    return {"total": total, "by_occupancy": by_occ}
