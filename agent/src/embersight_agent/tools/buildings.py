"""Microsoft Building Footprints + USA Structures spatial join.

Pass-2: pull per-state MS Building Footprints (GeoJSONL), reproject, and
intersect with USA Structures from FEMA/USGS for a richer attribute set.
"""

from __future__ import annotations

from typing import Any


async def buildings_in(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    raise NotImplementedError("pass-2: download MS Building Footprints tile and clip")
