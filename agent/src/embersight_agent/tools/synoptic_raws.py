"""RAWS observations via SynopticPy.

Pulls remote automated weather station (RAWS) observations — wind speed,
wind direction, RH, temperature — for stations inside a bounding box.

`SYNOPTIC_TOKEN` is required (see https://developers.synopticdata.com/).
The value can be either a Synoptic API token or an API key — if the call
fails with "Invalid token", we transparently exchange the key for a token
via Synoptic's `/v2/auth` endpoint and retry.

When the token is missing or the API errors, callers get an empty obs list
so the agent can degrade gracefully instead of crashing the whole graph.
"""

from __future__ import annotations

import asyncio
import math
import os
from datetime import timedelta
from typing import Any

import httpx

# Latitude is ~111 km per degree everywhere; longitude varies with latitude.
_KM_PER_DEG_LAT = 111.0


def bbox_around(lat: float, lon: float, radius_km: float = 50.0) -> tuple[float, float, float, float]:
    """Build a (lon_min, lat_min, lon_max, lat_max) bbox around a point.

    Returns the Synoptic-API-style ordering (lon-first). Longitude spacing is
    scaled by cos(lat) so the box is approximately square in real km.
    """
    dlat = radius_km / _KM_PER_DEG_LAT
    dlon = radius_km / (_KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-3))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _have_token() -> bool:
    return bool(os.environ.get("SYNOPTIC_TOKEN"))


_TOKEN_CACHE: dict[str, str] = {}


def _exchange_api_key_for_token(api_key: str) -> str | None:
    """Trade a Synoptic API *key* for a usable token via /v2/auth.

    Returns None if the exchange fails — callers fall back to the original
    value, which lets a real token still work.
    """
    try:
        resp = httpx.get(
            "https://api.synopticdata.com/v2/auth",
            params={"apikey": api_key},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("TOKEN")
    except Exception:  # noqa: BLE001
        return None


def _resolve_token() -> str | None:
    """Return a Synoptic *token*, exchanging from an API key if needed.

    Synoptic tokens are 32-char hex strings; longer/shorter values are
    treated as account API keys and exchanged via `/v2/auth`. The result
    is memoized to avoid a round-trip on every call.
    """
    raw = os.environ.get("SYNOPTIC_TOKEN")
    if not raw:
        return None
    cached = _TOKEN_CACHE.get(raw)
    if cached:
        return cached
    looks_like_token = len(raw) == 32 and all(c in "0123456789abcdef" for c in raw.lower())
    token = raw if looks_like_token else (_exchange_api_key_for_token(raw) or raw)
    _TOKEN_CACHE[raw] = token
    return token


def _fetch_sync(
    bbox: tuple[float, float, float, float],
    lookback_hours: int,
) -> dict[str, Any]:
    """Blocking SynopticPy call. Run via asyncio.to_thread."""
    from synoptic import services as synoptic_services

    bbox_str = ",".join(f"{v:.4f}" for v in bbox)
    token = _resolve_token()

    api = synoptic_services.TimeSeries(
        bbox=bbox_str,
        recent=timedelta(hours=lookback_hours),
        vars="wind_speed,wind_direction,wind_gust,air_temp,relative_humidity",
        units="english",
        token=token,
    )
    df = api.df()

    stations: dict[str, dict[str, Any]] = {}
    # SynopticPy returns a long-format polars DataFrame with one row per
    # (station, variable, timestamp). We collapse to the latest non-null value
    # per (station, variable) to give the agent a quick snapshot.
    if df is not None and df.height > 0:
        for row in df.iter_rows(named=True):
            stid = row.get("stid")
            var = row.get("variable")
            value = row.get("value")
            ts = row.get("date_time")
            if stid is None or var is None or value is None:
                continue
            station = stations.setdefault(
                stid,
                {
                    "stid": stid,
                    "name": row.get("name") or stid,
                    "latitude": row.get("latitude"),
                    "longitude": row.get("longitude"),
                    "elevation": row.get("elevation"),
                    "latest": {},
                },
            )
            latest = station["latest"]
            prior = latest.get(var)
            if prior is None or (ts and prior.get("timestamp") and ts > prior["timestamp"]):
                latest[var] = {"value": value, "timestamp": ts}

    return {
        "bbox": list(bbox),
        "lookback_hours": lookback_hours,
        "station_count": len(stations),
        "stations": list(stations.values()),
    }


async def get_raws_observations(
    bbox: tuple[float, float, float, float],
    lookback_hours: int = 24,
) -> dict[str, Any]:
    """Async wrapper around the (blocking) SynopticPy call.

    Returns an empty `stations` list if no token is configured or the API
    raises. The agent treats RAWS as supporting evidence — missing obs lower
    confidence but should not block the rest of the fusion.
    """
    if not _have_token():
        return {
            "bbox": list(bbox),
            "lookback_hours": lookback_hours,
            "station_count": 0,
            "stations": [],
            "error": "SYNOPTIC_TOKEN not set",
        }
    try:
        return await asyncio.to_thread(_fetch_sync, bbox, lookback_hours)
    except Exception as exc:  # noqa: BLE001
        return {
            "bbox": list(bbox),
            "lookback_hours": lookback_hours,
            "station_count": 0,
            "stations": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


# --------------------------------------------------------------------------- #
# Back-compat shim
# --------------------------------------------------------------------------- #


async def fetch_raws_near(
    lat: float, lon: float, radius_km: float = 50.0
) -> dict[str, Any]:
    """Legacy entrypoint: build a bbox around a point then fetch."""
    return await get_raws_observations(bbox_around(lat, lon, radius_km))


if __name__ == "__main__":
    async def _smoke() -> None:
        bbox = bbox_around(34.7402, -119.3142, 50.0)
        result = await get_raws_observations(bbox, lookback_hours=6)
        print(
            f"stations: {result['station_count']} "
            f"(error: {result.get('error', 'none')})"
        )

    asyncio.run(_smoke())
