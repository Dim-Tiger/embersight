"""NWS alerts (Red Flag Warning, Fire Weather Watch) + Spot Forecast.

User-Agent header is REQUIRED by the NWS API.
"""

from __future__ import annotations

import os

import httpx


def _ua() -> str:
    return os.environ.get(
        "NWS_USER_AGENT", "EmberSight Hackathon (contact@example.com)"
    )


async def fetch_active_alerts(lat: float, lon: float) -> dict:
    url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    headers = {"User-Agent": _ua(), "Accept": "application/geo+json"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()
