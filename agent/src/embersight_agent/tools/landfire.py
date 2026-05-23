"""LANDFIRE LFPS GP service wrapper (FBFM40 + slope + aspect + canopy).

Pass-2 will use the `landfire` PyPI package as a wrapper around the
submit/poll/fetch pattern at
https://lfps.usgs.gov/arcgis/rest/services/LandfireProductService/GPServer/LandfireProductService.
"""

from __future__ import annotations

from typing import Any


async def fetch_landfire(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    raise NotImplementedError("pass-2: implement with `landfire` package")
