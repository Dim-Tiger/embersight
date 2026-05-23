"""CAL FIRE IncidentApi + NIFC WFIGS merged incident feed."""

from __future__ import annotations

import httpx

CALFIRE_URL = (
    "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false"
)
WFIGS_POINTS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
    "?where=POOState%3D%27US-CA%27&outFields=*&f=geojson"
)
WFIGS_PERIMETERS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&f=geojson"
)


async def fetch_calfire_incidents() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(CALFIRE_URL)
        r.raise_for_status()
        return r.json()


async def fetch_wfigs_points() -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(WFIGS_POINTS_URL)
        r.raise_for_status()
        return r.json()


async def fetch_wfigs_perimeters() -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(WFIGS_PERIMETERS_URL)
        r.raise_for_status()
        return r.json()
