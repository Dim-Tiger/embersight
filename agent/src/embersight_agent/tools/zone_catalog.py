"""Static catalog of California evacuation-zone polygons.

The Cal OES ``California Active Evacuation Zones`` aggregation layer only
contains zones currently in WARNING or ORDER — typically <20 statewide on
any given day — and is therefore the wrong source for an agent that
needs to *propose* status changes against the full universe of zones in
the incident AOI.

This module fills that gap by harvesting publicly-shared per-county
evacuation-zone feeds:

* **Genasys / Zonehaven WFS** — each county that uses Zonehaven
  publishes an authkey-scoped layer (``z:evacuation_zone_status_CA``)
  via ``zms.zonehaven.com``. The authkeys themselves are intentionally
  baked into the URLs the counties register on ArcGIS Hub, so they are
  free for public consumption.
* **County-hosted ArcGIS Feature Services** — Cal OES, LA County, San
  Mateo County, Riverside County, etc. publish their full static zone
  set as ArcGIS FeatureServer layers.

Each source is fetched once and cached as GeoJSON under
``$EMBERSIGHT_ZONES_CACHE`` (default ``/tmp/embersight-zones-cache``)
for ``EMBERSIGHT_ZONES_TTL_HOURS`` (default 24). On cache miss the
fetch is best-effort: a single source failing logs a warning and is
skipped — the catalog only fails if *every* registered source fails.

All zone records are normalised to the same shape that
``evacuation_intelligence`` already consumes from ``evac.py``::

    {
        "zone_id":         str,
        "name":            str,
        "current_status":  "NORMAL" | "WARNING" | "ORDER",
        "polygon_wkt":     str,
        "last_updated_iso": str | None,
        "jurisdiction":    str,
        "source":          str,
        # optional enrichment from the source schema:
        "population_static": int | None,
        "structures_static": int | None,
    }

``current_status`` from a static source is always ``"NORMAL"``; live
status is overlaid by ``evac.get_active_status_overlay``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)

CACHE_DIR = Path(
    os.environ.get("EMBERSIGHT_ZONES_CACHE", "/tmp/embersight-zones-cache")
)
TTL_SECONDS = int(os.environ.get("EMBERSIGHT_ZONES_TTL_HOURS", "24")) * 3600


# --------------------------------------------------------------------------- #
# Source registry
# --------------------------------------------------------------------------- #
#
# Each entry: { key, kind, url, parser, jurisdiction_default? }
#   kind=="wfs"       — GeoServer WFS endpoint returning a FeatureCollection
#                       under outputFormat=application/json
#   kind=="esri"      — ArcGIS REST FeatureServer layer; we hit /query with
#                       f=geojson and a 1=1 where clause
#
# The authkeys embedded in the WFS URLs are *intentionally public* — every
# county that uses Zonehaven publishes their authkey on ArcGIS Hub under an
# item of type "WFS" so downstream apps (CalTopo, Watch Duty clones, etc.)
# can pull the polygons. See:
#   https://www.arcgis.com/sharing/rest/search?q=title:%22Genasys+Evacuation+Zones%22+type:%22WFS%22
#

GENASYS_WFS_BASE = "https://zms.zonehaven.com/geoserver/z/wfs"


def _wfs_url(authkey: str) -> str:
    return (
        f"{GENASYS_WFS_BASE}"
        f"?authkey={authkey}"
        "&service=WFS&request=GetFeature"
        "&typeNames=z:evacuation_zone_status_CA"
        "&outputFormat=application/json"
        "&srsName=EPSG:4326"
    )


SOURCES: list[dict[str, Any]] = [
    # PRIMARY: the canonical CalFire-owned statewide static catalog.
    # 26,081 zones with the full Genasys schema (zone_id, known_as,
    # county_abbr, est_population, etc). Public access, no authkey
    # required. This is what every CA county's Zonehaven roll-up
    # eventually feeds into.
    #   item: 1837ec14c1a64573b2dba13f976620ad  (ITS.CALFIRE)
    {
        "key": "calfire_all_zones",
        "kind": "esri",
        "url": (
            "https://services5.arcgis.com/bz1uwWPKUInZBK94/arcgis/rest/"
            "services/Evacuations_All_Zones_view/FeatureServer/0/query"
        ),
        "jurisdiction_default": "California",
        # This source uses the Genasys *property* schema (lowercase
        # zone_id, known_as, county_abbr, est_population) even though
        # it's served via an ArcGIS FeatureServer, so we parse with
        # the Genasys parser instead of the generic Esri one.
        "parser_kind": "wfs",
    },
    # SECONDARY backfills — kept around so the catalog still works if
    # the CalFire statewide service is down. Per-county sources sourced
    # from publicly-registered ArcGIS Hub items.
    {
        "key": "genasys_monterey_wfs",
        "kind": "wfs",
        "url": _wfs_url(
            "oB7GdQb8zO1Y0BqKATLAOD8caQ8AFbH2zTl1NqsqRTNRq4S9Tn7sJ3"
            "PThKQtaCbEQSRSXeW5mhan0CJP0iFqkdKx9bWMwiVkFxdYMUCUePKR"
            "zvmUz3uND3Qd0CibxDjl"
        ),
        "jurisdiction_default": "Monterey County",
    },
    {
        "key": "smc_zonehaven",
        "kind": "esri",
        "url": (
            "https://services.arcgis.com/yq3FgOI44hYHAFVZ/arcgis/rest/"
            "services/ZoneHaven_SMCEvacuationZones/FeatureServer/0/query"
        ),
        "jurisdiction_default": "San Mateo County",
    },
    # Cal OES *active* aggregation — harvested as a static source so
    # any zone currently in WARNING/ORDER is captured even if the
    # CalFire statewide layer happens to be stale. Status is set to
    # NORMAL here; the live status is reapplied by
    # ``evac.get_active_status_overlay`` so we have a single source of
    # truth for "what is active right now".
    {
        "key": "caloes_active_geometry",
        "kind": "esri",
        "url": (
            "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/"
            "services/CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query"
        ),
        "jurisdiction_default": "California (Cal OES aggregation)",
    },
]


# --------------------------------------------------------------------------- #
# Disk cache
# --------------------------------------------------------------------------- #


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.geojson"


def _cache_fresh(path: Path) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < TTL_SECONDS
    except FileNotFoundError:
        return False


def _read_cache(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        log.warning("zone_catalog: failed to write cache %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Geometry helpers (intentionally duplicated from evac.py to keep this
# module standalone — both convert Esri/GeoJSON rings into a single WKT
# POLYGON. evac.py predates this module; we don't want a cross-import
# cycle once that file is also touched.)
# --------------------------------------------------------------------------- #


def _ring_to_wkt(rings: list[list[list[float]]]) -> str:
    if not rings:
        return "POLYGON EMPTY"

    def ring_str(ring: list[list[float]]) -> str:
        return ", ".join(f"{pt[0]} {pt[1]}" for pt in ring)

    outer = ring_str(rings[0])
    if len(rings) == 1:
        return f"POLYGON (({outer}))"
    holes = ", ".join(f"({ring_str(r)})" for r in rings[1:])
    return f"POLYGON (({outer}), {holes})"


def _geojson_geom_to_wkt(geom: dict) -> str | None:
    if not geom:
        return None
    g_type = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if g_type == "Polygon":
        return _ring_to_wkt(coords)
    if g_type == "MultiPolygon":
        # Take the largest outer ring as primary. (For the agent's
        # overlap math an outer-ring approximation is sufficient — we
        # don't need true multipolygon semantics. Future pass should
        # union all parts via shapely.)
        try:
            largest = max(coords, key=lambda parts: len(parts[0]) if parts else 0)
            return _ring_to_wkt(largest)
        except (ValueError, IndexError):
            return _ring_to_wkt(coords[0])
    return None


# --------------------------------------------------------------------------- #
# Per-source parsers
# --------------------------------------------------------------------------- #


def _parse_feature_genasys(feat: dict, src_key: str, default_county: str) -> dict | None:
    """Parse one Genasys WFS feature.

    Schema observed from ``z:evacuation_zone_status_CA``: zone_id,
    zone_status, known_as, county_abbr, night_population, day_population,
    structures, acreage.
    """
    props = feat.get("properties") or {}
    wkt = _geojson_geom_to_wkt(feat.get("geometry") or {})
    if not wkt:
        return None
    zid = props.get("zone_id")
    if not zid:
        return None
    # `est_population` is the CalFire statewide layer's field name; the
    # raw Genasys WFS uses `night_population`/`day_population`. Accept
    # whichever is present.
    pop = (
        props.get("est_population")
        or props.get("night_population")
        or props.get("day_population")
    )

    # Most Genasys zones have a null `known_as`; the recognisable label
    # comes from the cross-streets ("north_of", "east_of", "south_of",
    # "west_of"). Build a fallback name like
    #   "north of Kanan Rd, east of Lobo Canyon Rd"
    # so the IC card has something human-readable.
    name = props.get("known_as")
    if not name:
        bits = []
        for dirn in ("north_of", "east_of", "south_of", "west_of"):
            v = props.get(dirn)
            if v:
                # Most labels list multiple streets — keep the first.
                first = str(v).split(",", 1)[0].strip()
                if first:
                    bits.append(f"{dirn.replace('_', ' ')} {first}")
            if len(bits) >= 2:
                break
        name = ", ".join(bits) if bits else f"Zone {zid}"

    return {
        "zone_id": str(zid),
        "name": str(name),
        "current_status": "NORMAL",
        "polygon_wkt": wkt,
        "last_updated_iso": props.get("last_updated"),
        "jurisdiction": _expand_county(props.get("county_abbr")) or default_county,
        "source": src_key,
        "population_static": int(pop) if isinstance(pop, (int, float)) else None,
        "structures_static": (
            int(props["structures"])
            if isinstance(props.get("structures"), (int, float))
            else None
        ),
    }


def _parse_feature_esri_geojson(
    feat: dict, src_key: str, default_county: str
) -> dict | None:
    """Parse one feature returned by an ArcGIS REST FeatureServer
    ``/query?f=geojson`` call.

    ArcGIS-as-GeoJSON puts attributes under ``properties`` with the
    original Esri field names. Different counties name things slightly
    differently — we try a list of common aliases.
    """
    props = feat.get("properties") or {}
    wkt = _geojson_geom_to_wkt(feat.get("geometry") or {})
    if not wkt:
        return None

    zid = (
        props.get("ZONE_ID")
        or props.get("Zone_ID")
        or props.get("ZoneID")
        or props.get("zone_id")
        or props.get("OBJECTID")
        or props.get("FID")
    )
    if zid is None:
        return None

    name = (
        props.get("ZONE_NAME")
        or props.get("Zone_Name")
        or props.get("ZoneName")
        or props.get("known_as")
        or props.get("ZONE_SHORT")  # San Mateo schema
        or props.get("ZONE_IDENT")  # San Mateo schema (fallback)
        or props.get("NAME")
        or props.get("Name")
        or f"Zone {zid}"
    )

    jurisdiction = (
        props.get("COUNTY")
        or props.get("County")
        or props.get("Jurisdiction")
        or props.get("Agency")
        or default_county
    )

    return {
        "zone_id": str(zid),
        "name": str(name),
        "current_status": "NORMAL",
        "polygon_wkt": wkt,
        "last_updated_iso": (
            props.get("EditDate")
            or props.get("last_edited_date")
            or props.get("STATEWIDE_LAST_UPDATED")
        ),
        "jurisdiction": str(jurisdiction),
        "source": src_key,
        "population_static": None,
        "structures_static": None,
    }


# Genasys 3-letter county abbreviations. Genasys uses two conventions:
#   - 3-letter county code  (e.g. MRY = Monterey)
#   - "X" + 2-letter code   (e.g. XMY = Monterey, XLA = Los Angeles, XSM
#                             = San Mateo, XTU = Tulare, XVE = Ventura)
# Both forms appear in the same statewide layer, so we register both.
# Expand to a human-readable jurisdiction so it surfaces nicely in the
# IC card. Unknowns are left as the raw abbr.
_COUNTY_ABBR_TO_NAME = {
    # Bay Area
    "XAL": "Alameda County", "ALA": "Alameda County",
    "XCC": "Contra Costa County", "CCC": "Contra Costa County",
    "XMR": "Marin County", "MRN": "Marin County",
    "XNA": "Napa County", "NAP": "Napa County",
    "XSF": "San Francisco", "SFO": "San Francisco",
    "XSM": "San Mateo County", "SMT": "San Mateo County",
    "XSC": "Santa Clara County", "SCL": "Santa Clara County",
    "XSL": "Solano County", "SOL": "Solano County",
    "XSO": "Sonoma County", "SON": "Sonoma County",
    # Central Coast
    "XMY": "Monterey County", "MRY": "Monterey County",
    "XSZ": "Santa Cruz County", "SCR": "Santa Cruz County",
    "XSB": "Santa Barbara County", "SBA": "Santa Barbara County",
    "XSL2": "San Luis Obispo County", "SLO": "San Luis Obispo County",
    "XVE": "Ventura County", "VEN": "Ventura County",
    # Southern California
    "XLA": "Los Angeles County", "LOS": "Los Angeles County",
    "XOR": "Orange County", "ORG": "Orange County",
    "XRI": "Riverside County", "RIV": "Riverside County",
    "XSD": "San Diego County", "SDG": "San Diego County",
    "XSN": "San Bernardino County", "SBR": "San Bernardino County",
    "XIM": "Imperial County", "IMP": "Imperial County",
    "XKE": "Kern County", "KER": "Kern County",
    # Central Valley
    "XSA": "Sacramento County", "SAC": "Sacramento County",
    "XSJ": "San Joaquin County", "SJQ": "San Joaquin County",
    "XST": "Stanislaus County", "STA": "Stanislaus County",
    "XME": "Merced County", "MER": "Merced County",
    "XMP": "Mariposa County", "MAR": "Mariposa County",
    "XMD": "Madera County", "MAD": "Madera County",
    "XFR": "Fresno County", "FRE": "Fresno County",
    "XKI": "Kings County", "KIN": "Kings County",
    "XTU": "Tulare County", "TUL": "Tulare County",
    "XYO": "Yolo County", "YOL": "Yolo County",
    "XSU": "Sutter County", "SUT": "Sutter County",
    "XYU": "Yuba County", "YUB": "Yuba County",
    "XCO": "Colusa County", "COL": "Colusa County",
    "XGL": "Glenn County", "GLE": "Glenn County",
    # Sierra / North
    "XPL": "Placer County", "PLA": "Placer County",
    "XED": "El Dorado County", "ELD": "El Dorado County",
    "XAM": "Amador County", "AMA": "Amador County",
    "XCA": "Calaveras County", "CAL": "Calaveras County",
    "XTC": "Tuolumne County", "TUO": "Tuolumne County",
    "XAL2": "Alpine County", "ALP": "Alpine County",
    "XMO": "Mono County", "MNO": "Mono County",
    "XIN": "Inyo County", "INY": "Inyo County",
    "XNE": "Nevada County", "NEV": "Nevada County",
    "XSI": "Sierra County", "SIE": "Sierra County",
    "XPU": "Plumas County", "PLU": "Plumas County",
    "XBU": "Butte County", "BUT": "Butte County",
    "XTE": "Tehama County", "TEH": "Tehama County",
    "XSH": "Shasta County", "SHA": "Shasta County",
    "XSK": "Siskiyou County", "SIS": "Siskiyou County",
    "XLA2": "Lassen County", "LAS": "Lassen County",
    "XMD2": "Modoc County", "MOD": "Modoc County",
    "XTI": "Trinity County", "TRI": "Trinity County",
    # Coastal North
    "XME2": "Mendocino County", "MEN": "Mendocino County",
    "XLK": "Lake County", "LAK": "Lake County",
    "XHU": "Humboldt County", "HUM": "Humboldt County",
    "XDN": "Del Norte County", "DEL": "Del Norte County",
    # Oregon spillover (Klamath County uses Genasys too)
    "KLM": "Klamath County",
}


def _expand_county(abbr: str | None) -> str | None:
    if not abbr:
        return None
    return _COUNTY_ABBR_TO_NAME.get(abbr.upper())


_PARSERS: dict[str, Callable[[dict, str, str], dict | None]] = {
    "wfs": _parse_feature_genasys,
    "esri": _parse_feature_esri_geojson,
}


# --------------------------------------------------------------------------- #
# Source fetcher
# --------------------------------------------------------------------------- #


async def _fetch_source(
    source: dict,
    *,
    client: httpx.AsyncClient,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """Fetch and parse one source. Returns normalised zone dicts.

    When ``bbox`` is supplied, ESRI sources are queried with a
    server-side spatial filter so we don't pull 26k polygons every time
    for a 10 km incident AOI. WFS sources still pull-then-filter because
    GeoServer's BBOX filter is awkward to assemble correctly.

    Caching: when bbox is given, ``cache_key = f"{source_key}_{bbox_hash}"``
    so a Ventura AOI doesn't poison a Monterey AOI's cache.
    """
    import json as _json  # noqa: PLC0415

    key = source["key"]
    if bbox is not None:
        # 4-decimal hash is fine — sub-10 m at CA latitudes
        bbox_key = "_".join(f"{v:.4f}" for v in bbox)
        cache_path = _cache_path(f"{key}__{bbox_key}")
    else:
        cache_path = _cache_path(key)

    # Try fresh cache first.
    if _cache_fresh(cache_path):
        raw = _read_cache(cache_path)
        if raw is not None:
            return _parse_features(source, raw)

    # Fetch live.
    try:
        if source["kind"] == "esri":
            params: dict[str, Any] = {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            }
            if bbox is not None:
                params["geometry"] = _json.dumps(
                    {
                        "xmin": bbox[0],
                        "ymin": bbox[1],
                        "xmax": bbox[2],
                        "ymax": bbox[3],
                        "spatialReference": {"wkid": 4326},
                    },
                    separators=(",", ":"),
                )
                params["geometryType"] = "esriGeometryEnvelope"
                params["inSR"] = "4326"
                params["spatialRel"] = "esriSpatialRelIntersects"
            r = await client.get(source["url"], params=params, timeout=30.0)
        else:
            r = await client.get(source["url"], timeout=30.0)
        r.raise_for_status()
        raw = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("zone_catalog: %s fetch failed (%s) — using stale cache if any",
                    key, exc)
        # Fall back to stale cache rather than empty.
        raw = _read_cache(cache_path)
        if raw is None:
            return []
        return _parse_features(source, raw)

    _write_cache(cache_path, raw)
    return _parse_features(source, raw)


def _parse_features(source: dict, raw: dict) -> list[dict]:
    # parser_kind lets a source use a different parser than its
    # transport kind — e.g. the CalFire statewide layer is served via
    # ArcGIS FeatureServer (kind="esri") but uses the lowercase Genasys
    # property schema (parser_kind="wfs").
    parser_kind = source.get("parser_kind", source["kind"])
    parser = _PARSERS.get(parser_kind)
    if parser is None:
        return []
    default_county = source.get("jurisdiction_default", "unknown")
    out: list[dict] = []
    for feat in (raw.get("features") or []):
        z = parser(feat, source["key"], default_county)
        if z is not None:
            out.append(z)
    return out


# --------------------------------------------------------------------------- #
# Bounding-box filter
# --------------------------------------------------------------------------- #


def _wkt_bbox(polygon_wkt: str) -> tuple[float, float, float, float] | None:
    """Rough WKT bbox via string parsing — avoids a shapely round-trip on
    every zone in the catalog. Returns ``(min_lon, min_lat, max_lon, max_lat)``
    or ``None`` if the WKT can't be parsed."""
    # Strip "POLYGON ((" prefix and trailing "))".
    try:
        body = polygon_wkt.split("((", 1)[1]
        body = body.rsplit("))", 1)[0]
    except (IndexError, AttributeError):
        return None

    xs: list[float] = []
    ys: list[float] = []
    # Coordinate pairs are "x y", separated by commas; holes are inside
    # parens — strip them so we get a flat sequence of pairs.
    flat = body.replace("(", " ").replace(")", " ")
    for pair in flat.split(","):
        parts = pair.strip().split()
        if len(parts) < 2:
            continue
        try:
            xs.append(float(parts[0]))
            ys.append(float(parts[1]))
        except ValueError:
            continue
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_intersects(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (
        a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3]
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def fetch_static_zones_in_bbox(
    bbox: tuple[float, float, float, float],
    *,
    timeout: float = 60.0,
) -> list[dict]:
    """Return all known static evacuation zones intersecting ``bbox``.

    ``bbox`` is ``(min_lon, min_lat, max_lon, max_lat)`` in WGS84.

    Catalog sources are queried in parallel; any source whose fetch
    fails is skipped with a warning. Zones are deduped by ``zone_id``
    (later sources win, since Genasys WFS schema is richer than ArcGIS
    Feature Services).
    """
    import asyncio  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_fetch_source(src, client=client, bbox=bbox) for src in SOURCES),
            return_exceptions=True,
        )

    all_zones: dict[str, dict] = {}
    for src, res in zip(SOURCES, results):
        if isinstance(res, BaseException):
            log.warning("zone_catalog: source %s raised: %s", src["key"], res)
            continue
        for z in res:
            zbb = _wkt_bbox(z["polygon_wkt"])
            if zbb is None or not _bbox_intersects(bbox, zbb):
                continue
            # Earlier sources are richer (Genasys WFS first); but if a
            # later source has population data and the earlier one
            # doesn't, prefer the richer record.
            existing = all_zones.get(z["zone_id"])
            if existing is None:
                all_zones[z["zone_id"]] = z
                continue
            if existing.get("population_static") is None and z.get("population_static"):
                all_zones[z["zone_id"]] = z

    return list(all_zones.values())


async def refresh_all_sources(*, timeout: float = 60.0) -> dict[str, int]:
    """Force-refresh every registered source. Returns ``{source_key:
    feature_count}``. Useful for a nightly warming cron and for tests."""
    import asyncio  # noqa: PLC0415

    # Invalidate caches by removing files; _fetch_source will refetch.
    for src in SOURCES:
        p = _cache_path(src["key"])
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_fetch_source(src, client=client) for src in SOURCES),
            return_exceptions=True,
        )

    return {
        src["key"]: (len(res) if not isinstance(res, BaseException) else 0)
        for src, res in zip(SOURCES, results)
    }
