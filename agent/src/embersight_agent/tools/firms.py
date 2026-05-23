"""NASA FIRMS VIIRS hotspot detections."""

from __future__ import annotations

import os

import httpx

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"


async def fetch_firms_ca(days: int = 1) -> str:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY not set")
    url = f"{BASE}/{key}/VIIRS_NOAA20_NRT/-124,32,-114,42/{days}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text
