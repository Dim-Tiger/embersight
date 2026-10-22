"""Open-Meteo current conditions at a point (no API key needed).

The dashboard's wind layer already reads Open-Meteo, so using it as the
agent's fallback keeps the agent's wind consistent with what the IC sees on
the map when the gridded HRRR/RTMA feeds (optional ``science`` group) and
RAWS (needs a token) are unavailable.

Units are requested in mph / °F so nothing needs converting; wind direction
is the meteorological FROM direction, like everywhere else in the agent.
"""

from __future__ import annotations

from typing import Any

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_S = 10.0


def parse_current(data: Any) -> dict[str, Any]:
    """Map an Open-Meteo ``current`` block onto the agent's flat keys."""
    current = (data or {}).get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        return {}
    mapping = {
        "wind_speed_10m": "wind_speed_mph",
        "wind_direction_10m": "wind_dir_deg",
        "wind_gusts_10m": "wind_gust_mph",
        "temperature_2m": "temp_f",
        "relative_humidity_2m": "rh_pct",
    }
    out: dict[str, Any] = {}
    for src, dst in mapping.items():
        value = current.get(src)
        if isinstance(value, (int, float)):
            out[dst] = float(value)
    if out:
        out["valid_time"] = current.get("time")
    return out


async def get_current(lat: float, lon: float) -> dict[str, Any]:
    """Current wind / temperature / RH. Raises on network or HTTP errors."""
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": (
            "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
            "temperature_2m,relative_humidity_2m"
        ),
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.get(OPEN_METEO_URL, params=params)
        r.raise_for_status()
        return parse_current(r.json())
