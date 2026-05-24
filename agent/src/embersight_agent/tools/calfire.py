"""NIFC WFIGS national incident feed, with optional CAL FIRE enrichment.

WFIGS (the National Interagency Fire Center's interagency feed) is the
national source of truth for active wildfire incidents and perimeters in
the US. It already includes CAL FIRE-managed incidents.

The CAL FIRE Umbraco API is California-only by design but carries some
fields (e.g. cooperator counts, state-specific incident URLs) that aren't
in WFIGS. We keep it as an enrichment that callers can opt into when the
AOI is in California.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

import httpx

CALFIRE_URL = (
    "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false"
)

_WFIGS_POINTS_BASE = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)
_WFIGS_PERIMETERS_BASE = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)


def _wfigs_where(states: list[str] | None) -> str:
    """Build a WFIGS `where` clause; None / empty means all-US."""
    if not states:
        return "1=1"
    # WFIGS POOState uses the 'US-XX' form (e.g. US-CA, US-OR).
    quoted = ",".join(f"'US-{s.upper()}'" for s in states)
    return f"POOState IN ({quoted})"


def _wfigs_points_url(states: list[str] | None = None) -> str:
    qs = urlencode({"where": _wfigs_where(states), "outFields": "*", "f": "geojson"}, quote_via=quote)
    return f"{_WFIGS_POINTS_BASE}?{qs}"


def _wfigs_perimeters_url(states: list[str] | None = None) -> str:
    qs = urlencode({"where": _wfigs_where(states), "outFields": "*", "f": "geojson"}, quote_via=quote)
    return f"{_WFIGS_PERIMETERS_BASE}?{qs}"


# Back-compat: pre-baked national URLs (no state filter).
WFIGS_POINTS_URL = _wfigs_points_url()
WFIGS_PERIMETERS_URL = _wfigs_perimeters_url()


async def fetch_calfire_incidents() -> list[dict]:
    """CA-only Umbraco feed. Use only when the AOI is in California."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(CALFIRE_URL)
        r.raise_for_status()
        return r.json()


async def fetch_wfigs_points(states: list[str] | None = None) -> dict:
    """National WFIGS incident points. Pass e.g. ['CA','OR'] to filter."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(_wfigs_points_url(states))
        r.raise_for_status()
        return r.json()


async def fetch_wfigs_perimeters(states: list[str] | None = None) -> dict:
    """National WFIGS perimeters. Pass e.g. ['CA','OR'] to filter."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(_wfigs_perimeters_url(states))
        r.raise_for_status()
        return r.json()
