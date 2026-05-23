"""Cal OES CA_EVACUATIONS feed (zone polygons + status)."""

from __future__ import annotations

import httpx

URL = (
    "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/"
    "CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&f=geojson"
)


async def fetch_evac_zones() -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(URL)
        r.raise_for_status()
        return r.json()
