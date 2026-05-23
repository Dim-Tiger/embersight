"""OpenStreetMap POI queries via Overpass (hydrants, fire stations)."""

from __future__ import annotations

import httpx

OVERPASS = "https://overpass-api.de/api/interpreter"


async def fetch_hydrants_and_stations(bbox: tuple[float, float, float, float]) -> dict:
    s, w, n, e = bbox
    q = f"""
    [out:json][timeout:25];
    (
      node["emergency"="fire_hydrant"]({s},{w},{n},{e});
      node["amenity"="fire_station"]({s},{w},{n},{e});
      way["amenity"="fire_station"]({s},{w},{n},{e});
    );
    out center;
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(OVERPASS, data={"data": q})
        r.raise_for_status()
        return r.json()
