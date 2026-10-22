"""Shared ArcGIS FeatureServer query helpers.

Every values-at-risk layer we hit (USA Structures, hospitals, schools,
transmission lines, critical facilities) is an ArcGIS ``/query`` endpoint.
They all accept the same spatial filter, so the filter is built in one
place:

* a ``(minx, miny, maxx, maxy)`` tuple becomes an envelope filter, and
* a WKT polygon becomes a true polygon filter, so counts describe the
  spread cone itself and not its bounding box. For a long, thin wind-driven
  cone the bounding box can hold several times as many buildings.

Requests are POSTed because a polygon with a few hundred vertices does not
fit in a GET URL.
"""

from __future__ import annotations

import json
from typing import Any, Union

import httpx

Area = Union[tuple[float, float, float, float], str]

DEFAULT_TIMEOUT_S = 20.0
# Vertex budget for the polygon sent to ArcGIS. The swept cone is a union of
# ~100 ellipses; simplifying to this many vertices changes its area by well
# under 1% and keeps the request small.
_MAX_VERTICES = 400


def _polygon_rings(polygon_wkt: str) -> list[list[list[float]]]:
    from shapely import wkt as _wkt  # noqa: PLC0415

    geom = _wkt.loads(polygon_wkt)
    if geom.is_empty:
        raise ValueError("empty polygon")
    tolerance = 0.0
    while _vertex_count(geom) > _MAX_VERTICES and tolerance < 0.05:
        tolerance = tolerance * 2 if tolerance else 1e-4
        geom = _wkt.loads(polygon_wkt).simplify(tolerance, preserve_topology=True)

    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    rings: list[list[list[float]]] = []
    for part in parts:
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        rings.append([[x, y] for x, y in part.exterior.coords])
        for hole in part.interiors:
            rings.append([[x, y] for x, y in hole.coords])
    if not rings:
        raise ValueError(f"no polygon rings in {geom.geom_type}")
    return rings


def _vertex_count(geom: Any) -> int:
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return sum(len(p.exterior.coords) for p in parts if hasattr(p, "exterior"))


def geometry_params(area: Area) -> dict[str, str]:
    """ArcGIS spatial-filter params for a bbox tuple or a WKT polygon."""
    if isinstance(area, str):
        geometry = {"rings": _polygon_rings(area), "spatialReference": {"wkid": 4326}}
        geometry_type = "esriGeometryPolygon"
    else:
        minx, miny, maxx, maxy = (float(v) for v in area)
        geometry = {
            "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
            "spatialReference": {"wkid": 4326},
        }
        geometry_type = "esriGeometryEnvelope"
    return {
        "geometry": json.dumps(geometry, separators=(",", ":")),
        "geometryType": geometry_type,
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def query(
    url: str,
    params: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST an ArcGIS ``/query`` and return the JSON body.

    ArcGIS answers HTTP 200 with an ``{"error": ...}`` body when a layer is
    missing or a parameter is wrong; that is raised as a failure too, so a
    broken layer can never look like "zero features found".
    """
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, data=params)
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and "error" in data and "features" not in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"arcgis:{msg}")
    return data
