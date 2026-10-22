"""OpenStreetMap queries via the Overpass API.

The routing & staging agent and the rally-point finder use these wrappers
to find fire stations, water sources, paved staging candidates and
evacuation destinations around an incident.

The public Overpass servers are shared, donated infrastructure with a usage
policy (https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances):
identify yourself, don't run queries in parallel, back off when told to.
This module enforces that for every caller:

* **One request at a time.** A process-wide lock serialises all Overpass
  calls, even when agents run concurrently.
* **User-Agent** on every request.
* **Bounded retry with backoff.** 429 / 502 / 503 / 504 / timeouts are
  retried after ``Retry-After`` (or an exponential delay), rotating to the
  next mirror, up to ``MAX_ATTEMPTS`` tries and ``CALL_BUDGET_S`` seconds.
  After a full outage the network is skipped for ``DOWN_FOR_S`` so the next
  queries fail fast instead of each waiting out the same dead servers.
* **On-disk cache** keyed by the exact query text. A fresh copy (younger
  than ``CACHE_TTL_S``) is served without touching the network, and if
  every mirror fails a stale copy of any age is served instead, so a repeat
  run of the same incident works offline.

bbox convention everywhere in this module: ``(south, west, north, east)``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Primary first. Mirrors carry the full planet (overpass.osm.ch, for example,
# only serves Switzerland and would silently return nothing for California).
DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS = DEFAULT_ENDPOINTS[0]
DEFAULT_TIMEOUT_S = 30.0
OVERPASS_TIMEOUT_S = 25  # server-side timeout passed into the QL
USER_AGENT = "EmberSight/0.2 (wildfire incident decision support; +https://github.com/Dim-Tiger/embersight)"

MAX_ATTEMPTS = 4
MAX_BACKOFF_S = 8.0
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
# Wall-clock cap for one query including retries, and how long to skip the
# network after a query has exhausted every mirror. Without these, five
# queries against dead servers took ~10 minutes before routing gave up.
CALL_BUDGET_S = 45.0
DOWN_FOR_S = 120.0
_down_until = 0.0
CACHE_TTL_S = float(os.environ.get("EMBERSIGHT_OVERPASS_TTL_HOURS", "6")) * 3600


class OverpassUnavailable(RuntimeError):
    """Every mirror failed and no cached copy exists."""


def endpoints() -> list[str]:
    raw = os.environ.get("EMBERSIGHT_OVERPASS_URLS", "")
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls or list(DEFAULT_ENDPOINTS)


def _cache_dir() -> Path:
    return Path(os.environ.get("EMBERSIGHT_OVERPASS_CACHE", "/tmp/embersight-overpass-cache"))


def _cache_path(query: str) -> Path:
    return _cache_dir() / f"{hashlib.sha1(query.encode('utf-8')).hexdigest()}.json"


def _read_cache(query: str, *, max_age_s: float | None) -> dict[str, Any] | None:
    path = _cache_path(query)
    try:
        age = time.time() - path.stat().st_mtime
        if max_age_s is not None and age > max_age_s:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(query: str, data: dict[str, Any]) -> None:
    path = _cache_path(query)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError as exc:  # a read-only disk must not break the agent
        log.warning("overpass cache write failed: %s", exc)


# One lock per event loop: asyncio locks cannot be shared across loops, and
# tests (asyncio.run) create a fresh loop each time.
_LOCKS: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _LOCKS.get(loop_id)
    if lock is None:
        lock = _LOCKS[loop_id] = asyncio.Lock()
    return lock


def _retry_after_s(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), MAX_BACKOFF_S)
    return min(2.0 ** attempt, MAX_BACKOFF_S)


async def _sleep(seconds: float) -> None:
    # Indirection so tests can skip real waiting.
    await asyncio.sleep(seconds)


def _serve_stale_or_raise(query: str, errors: list[str]) -> dict[str, Any]:
    stale = _read_cache(query, max_age_s=None)
    if stale is not None:
        log.warning("Overpass unavailable (%s); serving stale cached result", "; ".join(errors))
        return stale
    raise OverpassUnavailable("; ".join(errors) or "no Overpass endpoint configured")


async def _post(query: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    global _down_until
    cached = _read_cache(query, max_age_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    urls = endpoints()
    errors: list[str] = []
    async with _lock():
        # Circuit breaker: right after every mirror failed, don't spend
        # another minute rediscovering that for each of the next queries.
        if time.monotonic() < _down_until:
            return _serve_stale_or_raise(query, ["Overpass marked down after recent failures"])
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            for attempt in range(MAX_ATTEMPTS):
                if time.monotonic() - started > CALL_BUDGET_S:
                    errors.append(f"gave up after {CALL_BUDGET_S:.0f} s")
                    break
                url = urls[attempt % len(urls)]
                response: httpx.Response | None = None
                try:
                    response = await client.post(url, data={"data": query})
                    if response.status_code in RETRYABLE_STATUS:
                        raise httpx.HTTPStatusError(
                            f"HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    data = response.json()
                    _write_cache(query, data)
                    _down_until = 0.0
                    return data
                except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                    status = response.status_code if response is not None else None
                    errors.append(f"{url} -> {status or type(exc).__name__}")
                    retryable = status is None or status in RETRYABLE_STATUS
                    if not retryable:
                        # Our query is wrong (e.g. 400); the servers are fine.
                        return _serve_stale_or_raise(query, errors)
                    if attempt == MAX_ATTEMPTS - 1:
                        break
                    await _sleep(_retry_after_s(response, attempt))
        _down_until = time.monotonic() + DOWN_FOR_S

    return _serve_stale_or_raise(query, errors)


def _bbox_clause(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return f"({s},{w},{n},{e})"


def _filter_clause(key: str, values: list[str]) -> str:
    if len(values) == 1:
        return f'["{key}"="{values[0]}"]'
    pattern = "|".join(values)
    return f'["{key}"~"^({pattern})$"]'


Clause = tuple[str, list[str], tuple[str, ...]]


def _build_union_query(bbox: tuple[float, float, float, float], clauses: list[Clause]) -> str:
    """One Overpass request for several tag filters (a union), so a caller
    that needs five feature types costs the shared server one query, not five."""
    bbox_str = _bbox_clause(bbox)
    parts = [
        f"{etype}{_filter_clause(key, values)}{bbox_str};"
        for key, values, etypes in clauses
        for etype in etypes
    ]
    body = "\n      ".join(parts)
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];\n"
        f"    (\n      {body}\n    );\n"
        f"    out center tags;\n"
    )


def _build_query(
    bbox: tuple[float, float, float, float],
    filters: dict[str, list[str]],
    element_types: tuple[str, ...] = ("node", "way", "relation"),
) -> str:
    return _build_union_query(
        bbox, [(key, values, element_types) for key, values in filters.items()]
    )


async def query_osm(
    bbox: tuple[float, float, float, float],
    filters: dict[str, list[str]],
    *,
    element_types: tuple[str, ...] = ("node", "way", "relation"),
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Generic Overpass query.

    ``filters`` maps an OSM key to one or more accepted values, e.g.
    ``{"highway": ["primary", "secondary", "tertiary"]}``.
    Returns the ``elements`` list from the Overpass JSON response.
    Raises ``OverpassUnavailable`` when no mirror answers and nothing is cached.
    """
    query = _build_query(bbox, filters, element_types=element_types)
    data = await _post(query, timeout=timeout)
    return data.get("elements", [])


async def query_osm_union(
    bbox: tuple[float, float, float, float],
    clauses: list[Clause],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Like ``query_osm`` but each clause has its own element types."""
    data = await _post(_build_union_query(bbox, clauses), timeout=timeout)
    return data.get("elements", [])


async def get_water_features(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Lakes / reservoirs / streams / rivers — candidate water supply."""
    return await query_osm_union(
        bbox,
        [
            ("natural", ["water"], ("node", "way", "relation")),
            ("waterway", ["stream", "river", "canal"], ("way",)),
        ],
    )


async def get_fire_stations(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """All ``amenity=fire_station`` features in the bbox."""
    return await query_osm(
        bbox,
        {"amenity": ["fire_station"]},
        element_types=("node", "way"),
    )


async def get_paved_areas(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Candidate staging surfaces: industrial / parking / commercial land
    parcels plus explicit ``surface=paved`` highways."""
    return await query_osm_union(
        bbox,
        [
            ("landuse", ["industrial", "commercial"], ("way", "relation")),
            ("amenity", ["parking"], ("node", "way", "relation")),
            ("surface", ["paved", "asphalt", "concrete"], ("way",)),
        ],
    )


# Kept for backwards compatibility with earlier pass-1 callers.
async def fetch_hydrants_and_stations(
    bbox: tuple[float, float, float, float],
) -> dict[str, Any]:
    s, w, n, e = bbox
    q = f"""
    [out:json][timeout:{OVERPASS_TIMEOUT_S}];
    (
      node["emergency"="fire_hydrant"]({s},{w},{n},{e});
      node["amenity"="fire_station"]({s},{w},{n},{e});
      way["amenity"="fire_station"]({s},{w},{n},{e});
    );
    out center;
    """
    return await _post(q)
