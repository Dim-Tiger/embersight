"""NWS alerts (Red Flag Warning, Fire Weather Watch) + forecast.

User-Agent header is REQUIRED by the NWS API. Set NWS_USER_AGENT in the env
to identify your traffic, otherwise we fall back to a generic hackathon UA.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

NWS_BASE = "https://api.weather.gov"
_DEFAULT_TIMEOUT = 15.0


def _ua() -> str:
    return os.environ.get(
        "NWS_USER_AGENT", "EmberSight Hackathon (contact@example.com)"
    )


def _headers() -> dict[str, str]:
    return {"User-Agent": _ua(), "Accept": "application/geo+json"}


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    r = await client.get(url)
    r.raise_for_status()
    return r.json()


async def get_forecast(lat: float, lon: float) -> dict[str, Any]:
    """Return the NWS hourly forecast for a point.

    Two-hop call: /points/{lat},{lon} resolves the gridpoint, then we follow
    the `forecastHourly` URL it returns. The shape mirrors NWS GeoJSON so the
    caller can pluck `properties.periods[*]` directly.
    """
    points_url = f"{NWS_BASE}/points/{lat:.4f},{lon:.4f}"
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, headers=_headers()
    ) as client:
        points = await _get_json(client, points_url)
        hourly_url = (
            points.get("properties", {}).get("forecastHourly")
            or points.get("properties", {}).get("forecast")
        )
        if not hourly_url:
            return {"forecast": None, "points": points}
        hourly = await _get_json(client, hourly_url)
        return {
            "forecast": hourly,
            "grid_id": points.get("properties", {}).get("gridId"),
            "grid_x": points.get("properties", {}).get("gridX"),
            "grid_y": points.get("properties", {}).get("gridY"),
            "forecast_url": hourly_url,
        }


async def get_active_alerts(lat: float, lon: float) -> list[dict[str, Any]]:
    """Return the list of active NWS alerts intersecting a point.

    Useful for surfacing Red Flag Warnings and Fire Weather Watches. The
    underlying endpoint returns a GeoJSON FeatureCollection — we flatten the
    `features` array because that's what callers actually iterate over.
    """
    url = f"{NWS_BASE}/alerts/active?point={lat:.4f},{lon:.4f}"
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, headers=_headers()
    ) as client:
        data = await _get_json(client, url)
    return list(data.get("features", []))


# --------------------------------------------------------------------------- #
# Back-compat shim
# --------------------------------------------------------------------------- #


async def fetch_active_alerts(lat: float, lon: float) -> dict[str, Any]:
    """Legacy entrypoint used elsewhere in the repo. Returns the raw envelope."""
    url = f"{NWS_BASE}/alerts/active?point={lat:.4f},{lon:.4f}"
    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT, headers=_headers()
    ) as client:
        return await _get_json(client, url)


if __name__ == "__main__":
    # Quick sanity check against Los Padres NF.
    async def _smoke() -> None:
        forecast, alerts = await asyncio.gather(
            get_forecast(34.7402, -119.3142),
            get_active_alerts(34.7402, -119.3142),
        )
        periods = (forecast.get("forecast") or {}).get("properties", {}).get(
            "periods", []
        )
        print(f"forecast periods: {len(periods)}")
        print(f"active alerts:    {len(alerts)}")

    asyncio.run(_smoke())
