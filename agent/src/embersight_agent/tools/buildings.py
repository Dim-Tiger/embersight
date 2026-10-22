"""Building counts inside a polygon (FEMA USA Structures).

FEMA's USA Structures layer bundles Microsoft Building Footprints with an
occupancy class (Residential, Commercial, ...). Both helpers below ask the
FeatureServer for *statistics* (counts and sums computed server-side) with
the spread cone as a polygon filter, so the numbers are exact for the cone:

* no 2,000-feature page limit (the old feature download silently capped
  ``count`` at 2,000), and
* no bounding-box inflation (the old envelope filter counted every building
  in the cone's bounding box).

Heavy geo dependencies (shapely) are lazy-imported inside ``tools.arcgis``.
"""

from __future__ import annotations

import json
from typing import Any

from .arcgis import Area, geometry_params, query

# FEMA USA Structures FeatureServer (polygon footprints with OCC_CLS).
USA_STRUCTURES_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)

# Microsoft Building Footprints — Planetary Computer STAC root, kept here as
# documentation of where the footprints inside USA Structures come from.
MS_BUILDINGS_STAC = (
    "https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
    "ms-buildings"
)


def _stats_params(area: Area, where: str, stats: list[dict], group_by: str | None) -> dict:
    params = {
        "where": where,
        **geometry_params(area),
        "outStatistics": json.dumps(stats, separators=(",", ":")),
        "f": "json",
    }
    if group_by:
        params["groupByFieldsForStatistics"] = group_by
    return params


def query_ms_buildings(area: Area) -> dict[str, Any]:
    """Count building footprints in ``area`` and sum their footprint area.

    ``area`` is a WKT polygon (preferred) or a ``(minx, miny, maxx, maxy)``
    bbox. Returns ``{count, total_footprint_sqm}`` or ``{..., error}``.
    """
    try:
        params = _stats_params(
            area,
            "1=1",
            [
                {"statisticType": "count", "onStatisticField": "OBJECTID",
                 "outStatisticFieldName": "n"},
                {"statisticType": "sum", "onStatisticField": "SQMETERS",
                 "outStatisticFieldName": "sqm"},
            ],
            None,
        )
        data = query(USA_STRUCTURES_URL, params)
    except Exception as exc:  # noqa: BLE001
        return {"count": 0, "total_footprint_sqm": 0.0, "error": f"http:{exc}"}

    rows = data.get("features") or []
    attrs = (rows[0].get("attributes") or {}) if rows else {}
    return {
        "count": int(attrs.get("n") or 0),
        "total_footprint_sqm": round(float(attrs.get("sqm") or 0.0), 1),
    }


def query_usa_structures(area: Area) -> dict[str, Any]:
    """USA Structures occupancy-class breakdown for ``area``.

    Returns::

      {total: int, by_occupancy: {Residential: N, Commercial: N, ...}, error?: str}
    """
    try:
        params = _stats_params(
            area,
            "OCC_CLS IS NOT NULL",
            [{"statisticType": "count", "onStatisticField": "OCC_CLS",
              "outStatisticFieldName": "n"}],
            "OCC_CLS",
        )
        data = query(USA_STRUCTURES_URL, params)
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "by_occupancy": {}, "error": f"http:{exc}"}

    by_occ: dict[str, int] = {}
    for row in data.get("features", []) or []:
        attrs = row.get("attributes", {}) or {}
        cls = attrs.get("OCC_CLS") or "Unknown"
        n = int(attrs.get("n", 0) or 0)
        by_occ[cls] = by_occ.get(cls, 0) + n
    return {"total": sum(by_occ.values()), "by_occupancy": by_occ}
