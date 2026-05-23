"""OpenStreetMap queries via the Overpass API.

Thin async wrappers around https://overpass-api.de/api/interpreter that
return the parsed Overpass JSON `elements` list. The routing & staging
agent uses these to find fire stations, water sources, paved staging
candidates, and the road network surrounding an incident AOI.

bbox convention everywhere in this module: ``(south, west, north, east)``.
"""

from __future__ import annotations

from typing import Any

import httpx

OVERPASS = "https://overpass-api.de/api/interpreter"
DEFAULT_TIMEOUT_S = 30.0
OVERPASS_TIMEOUT_S = 25  # server-side timeout passed into the QL
USER_AGENT = "EmberSight/0.1 (+https://github.com/Dim-Tiger/embersight)"


def _bbox_clause(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return f"({s},{w},{n},{e})"


def _filter_clause(key: str, values: list[str]) -> str:
    if len(values) == 1:
        return f'["{key}"="{values[0]}"]'
    pattern = "|".join(values)
    return f'["{key}"~"^({pattern})$"]'


def _build_query(
    bbox: tuple[float, float, float, float],
    filters: dict[str, list[str]],
    element_types: tuple[str, ...] = ("node", "way", "relation"),
) -> str:
    bbox_str = _bbox_clause(bbox)
    parts: list[str] = []
    for key, values in filters.items():
        clause = _filter_clause(key, values)
        for etype in element_types:
            parts.append(f"{etype}{clause}{bbox_str};")
    body = "\n      ".join(parts)
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];\n"
        f"    (\n      {body}\n    );\n"
        f"    out center tags;\n"
    )


async def _post(query: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    # Overpass rejects default httpx UA with 406; identify ourselves.
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        r = await client.post(OVERPASS, data={"data": query})
        r.raise_for_status()
        return r.json()


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
    """
    query = _build_query(bbox, filters, element_types=element_types)
    data = await _post(query, timeout=timeout)
    return data.get("elements", [])


async def get_water_features(
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Lakes / reservoirs / streams / rivers — candidate water supply."""
    elements = []
    elements += await query_osm(bbox, {"natural": ["water"]})
    elements += await query_osm(
        bbox,
        {"waterway": ["stream", "river", "canal"]},
        element_types=("way",),
    )
    return elements


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
    landuse = await query_osm(
        bbox,
        {"landuse": ["industrial", "commercial"]},
        element_types=("way", "relation"),
    )
    parking = await query_osm(
        bbox,
        {"amenity": ["parking"]},
        element_types=("node", "way", "relation"),
    )
    paved_roads = await query_osm(
        bbox,
        {"surface": ["paved", "asphalt", "concrete"]},
        element_types=("way",),
    )
    return landuse + parking + paved_roads


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
