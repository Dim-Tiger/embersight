"""WFIGS Interagency Perimeters — fetch the current fire perimeter polygon.

Mirrors the contract used by web/app/api/perimeter/route.ts so the agent and
the frontend pull the same source-of-truth perimeter geometry. We prefer the
IrwinID-keyed lookup (precise) and fall back to a point-intersect query when
no IrwinID is available.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

WFIGS_PERIMETERS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)

_IRWIN_RE = re.compile(r"^[{]?[0-9a-fA-F-]{32,40}[}]?$")


def _is_safe_irwin(s: str) -> bool:
    return bool(_IRWIN_RE.match(s))


def fetch_perimeter(
    *,
    irwin_id: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    timeout: float = 15.0,
) -> dict | None:
    """Return a GeoJSON FeatureCollection, or None when nothing matches.

    Parameters mirror the frontend route exactly so the agent's spread cone is
    swept over the same perimeter the operator is looking at on the map.
    """
    params: dict[str, str]
    if irwin_id and _is_safe_irwin(irwin_id):
        params = {
            "where": f"poly_IRWINID='{irwin_id}'",
            "outFields": "poly_IRWINID,poly_IncidentName,poly_GISAcres",
            "f": "geojson",
        }
    elif lat is not None and lon is not None:
        params = {
            "where": "1=1",
            "geometry": f'{{"x":{lon},"y":{lat}}}',
            "geometryType": "esriGeometryPoint",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "outFields": "poly_IRWINID,poly_IncidentName,poly_GISAcres",
            "f": "geojson",
        }
    else:
        return None

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(WFIGS_PERIMETERS_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(data, dict) or not data.get("features"):
        return None
    return data


def perimeter_to_shapely(perimeter: dict | None) -> Any:
    """Merge all perimeter polygons into a single shapely (Multi)Polygon, or
    None if the input is empty/invalid."""
    if not perimeter or not perimeter.get("features"):
        return None
    from shapely.geometry import shape  # noqa: PLC0415
    from shapely.ops import unary_union  # noqa: PLC0415

    geoms = []
    for f in perimeter["features"]:
        g = f.get("geometry")
        if not g:
            continue
        try:
            geoms.append(shape(g))
        except Exception:  # noqa: BLE001
            continue
    if not geoms:
        return None
    merged = unary_union(geoms)
    return merged if not merged.is_empty else None
