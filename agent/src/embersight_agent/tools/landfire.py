"""LANDFIRE ImageServer wrapper (FBFM40 + slope + aspect + elevation + canopy).

USGS publishes the same raster atlas through two paths:
  - the LFPS GP service (async, queued, ~2 min per layer), and
  - per-layer ArcGIS ImageServers with synchronous `exportImage` (~1 sec each).

We use the ImageServer path. Each public helper is synchronous; callers in
async code wrap them with `asyncio.to_thread` so the five layers fetch in
parallel and the whole pull finishes in ~3-4 sec on a cold cache.

Rasters are cached under /tmp/landfire-cache/<bbox-hash>/<layer>.tif so
repeated runs in a dev session are served from disk.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any

# Logical layer names — also used as cache keys, so existing on-disk caches
# stay compatible across the LFPS→ImageServer rewrite.
FBFM40_LAYER = "LF2022_FBFM40"
CANOPY_COVER_LAYER = "LF2022_CC"
SLOPE_DEG_LAYER = "LF2020_SlpD"
ASPECT_LAYER = "LF2020_Asp"
ELEVATION_LAYER = "LF2020_Elev"

LANDFIRE_VERSION = "LF 2022 (fuel/canopy) + LF 2020 (topographic)"

# Per-layer ImageServer service paths (CONUS variant — AK/HI not needed for CA).
_IMAGE_SERVER_BASE = "https://lfps.usgs.gov/arcgis/rest/services"
_LAYER_SERVICES: dict[str, str] = {
    FBFM40_LAYER:       "Landfire_LF2022/LF2022_FBFM40_CONUS",
    CANOPY_COVER_LAYER: "Landfire_LF2022/LF2022_CC_CONUS",
    SLOPE_DEG_LAYER:    "Landfire_Topo/LF2020_SlpD_CONUS",
    ASPECT_LAYER:       "Landfire_Topo/LF2020_Asp_CONUS",
    ELEVATION_LAYER:    "Landfire_Topo/LF2020_Elev_CONUS",
}
LANDFIRE_SOURCE_URL = _IMAGE_SERVER_BASE
LANDFIRE_USER_AGENT = "embersight-agent (+https://github.com/Dim-Tiger/embersight)"

CACHE_DIR = Path(os.environ.get("EMBERSIGHT_LANDFIRE_CACHE", "/tmp/landfire-cache"))

# exportImage requires a pixel size. LANDFIRE rasters are native 30 m; clamp to
# a square that respects native resolution while staying under ArcGIS's
# practical 4096-pixel ceiling.
_NATIVE_RES_M = 30.0
_MAX_PIXELS = 2048

# Hard ceiling on a single exportImage call — even cold network paths return
# in seconds, so anything past this is a hung server, not a slow one.
_EXPORT_TIMEOUT_SEC = float(os.environ.get("EMBERSIGHT_LANDFIRE_TIMEOUT", "30"))

# Scott & Burgan FBFM40 pixel value -> short label.
# Reference: Scott & Burgan 2005, RMRS-GTR-153; LANDFIRE uses the same codes
# for the 2022 FBFM40 raster (values outside this map are treated as "unknown").
FBFM40_CODE_TO_LABEL: dict[int, str] = {
    91: "NB1 urban/developed",
    92: "NB2 snow/ice",
    93: "NB3 agriculture",
    98: "NB8 open water",
    99: "NB9 bare ground",
    101: "GR1 short, sparse dry climate grass",
    102: "GR2 low load, dry climate grass",
    103: "GR3 low load, very coarse grass",
    104: "GR4 moderate load, dry climate grass",
    105: "GR5 low load, humid climate grass",
    106: "GR6 moderate load, humid grass",
    107: "GR7 high load, dry climate grass",
    108: "GR8 high load, very coarse grass",
    109: "GR9 very high load, humid grass",
    121: "GS1 low load, dry climate grass-shrub",
    122: "GS2 moderate load, dry grass-shrub",
    123: "GS3 moderate load, humid grass-shrub",
    124: "GS4 high load, humid grass-shrub",
    141: "SH1 low load, dry climate shrub",
    142: "SH2 moderate load, dry shrub",
    143: "SH3 moderate load, humid shrub",
    144: "SH4 low load, humid shrub",
    145: "SH5 high load, dry shrub",
    146: "SH6 low load, humid shrub",
    147: "SH7 very high load, dry shrub",
    148: "SH8 high load, humid shrub",
    149: "SH9 very high load, humid shrub",
    161: "TU1 light load, dry timber-grass-shrub",
    162: "TU2 moderate load, humid timber-shrub",
    163: "TU3 moderate load, humid timber-grass-shrub",
    164: "TU4 dwarf conifer with understory",
    165: "TU5 very high load, dry timber-shrub",
    181: "TL1 low load, compact conifer litter",
    182: "TL2 low load, broadleaf litter",
    183: "TL3 moderate load, conifer litter",
    184: "TL4 small downed logs",
    185: "TL5 high load, conifer litter",
    186: "TL6 moderate load, broadleaf litter",
    187: "TL7 large downed logs",
    188: "TL8 long-needle litter",
    189: "TL9 very high load, broadleaf litter",
    201: "SB1 low load activity fuel",
    202: "SB2 moderate load activity fuel",
    203: "SB3 high load activity fuel",
    204: "SB4 high load blowdown",
}


# --------------------------------------------------------------------------- #
# Bbox utilities
# --------------------------------------------------------------------------- #


def _normalize_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Return bbox as (min_lon, min_lat, max_lon, max_lat) with swaps if needed."""
    x1, y1, x2, y2 = bbox
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _bbox_str(bbox: tuple[float, float, float, float]) -> str:
    """LANDFIRE expects 'min_x min_y max_x max_y'."""
    x1, y1, x2, y2 = _normalize_bbox(bbox)
    return f"{x1} {y1} {x2} {y2}"


def _bbox_hash(bbox: tuple[float, float, float, float]) -> str:
    return hashlib.sha1(_bbox_str(bbox).encode()).hexdigest()[:12]


def bbox_around(lat: float, lon: float, km: float = 50.0) -> tuple[float, float, float, float]:
    """Build a roughly km-radius bbox in lon/lat degrees around a point."""
    deg_lat = km / 111.0
    deg_lon = km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
    return (lon - deg_lon, lat - deg_lat, lon + deg_lon, lat + deg_lat)


# --------------------------------------------------------------------------- #
# Raster fetch + cache
# --------------------------------------------------------------------------- #


def _cache_path_for(bbox: tuple[float, float, float, float], layer: str) -> Path:
    sub = CACHE_DIR / _bbox_hash(bbox)
    sub.mkdir(parents=True, exist_ok=True)
    return sub / f"{layer}.tif"


def _pixel_size(bbox: tuple[float, float, float, float]) -> tuple[int, int]:
    """Pick a (width, height) for exportImage that respects native 30 m resolution.

    Degrees → metres uses cos(lat) for longitude and a flat 111 km for latitude.
    Result is clamped to `_MAX_PIXELS` per axis to keep ArcGIS happy.
    """
    x1, y1, x2, y2 = _normalize_bbox(bbox)
    mid_lat = (y1 + y2) / 2.0
    width_m = (x2 - x1) * 111_000.0 * max(math.cos(math.radians(mid_lat)), 0.1)
    height_m = (y2 - y1) * 111_000.0
    w = max(8, min(_MAX_PIXELS, int(round(width_m / _NATIVE_RES_M))))
    h = max(8, min(_MAX_PIXELS, int(round(height_m / _NATIVE_RES_M))))
    return w, h


def _export_image(bbox: tuple[float, float, float, float], layer: str, dest: Path) -> None:
    """Sync GET against the layer's ImageServer/exportImage; write a GeoTIFF."""
    import requests  # lazy

    service = _LAYER_SERVICES.get(layer)
    if service is None:
        raise ValueError(f"no ImageServer mapping for layer {layer!r}")

    x1, y1, x2, y2 = _normalize_bbox(bbox)
    w, h = _pixel_size(bbox)
    params = {
        "bbox": f"{x1},{y1},{x2},{y2}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{w},{h}",
        "format": "tiff",
        "interpolation": "RSP_NearestNeighbor",  # categorical FBFM40/aspect must not interpolate
        "f": "image",
    }
    url = f"{_IMAGE_SERVER_BASE}/{service}/ImageServer/exportImage"
    headers = {"User-Agent": LANDFIRE_USER_AGENT}

    with requests.get(
        url, params=params, headers=headers, stream=True, timeout=_EXPORT_TIMEOUT_SEC
    ) as r:
        r.raise_for_status()
        # ArcGIS quietly returns HTML on errors with a 200 — verify content type.
        ctype = r.headers.get("Content-Type", "")
        if "tiff" not in ctype.lower():
            body = r.content[:400].decode("utf-8", "replace")
            raise RuntimeError(
                f"exportImage for {layer} returned non-TIFF ({ctype}): {body}"
            )
        with open(dest, "wb") as out:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    out.write(chunk)


def _fetch_layer(bbox: tuple[float, float, float, float], layer: str) -> Path:
    """Download a single LANDFIRE layer to the local cache; return the .tif path."""
    tif_path = _cache_path_for(bbox, layer)
    if tif_path.exists() and tif_path.stat().st_size > 0:
        return tif_path

    tmp_path = tif_path.with_suffix(".tif.partial")
    try:
        _export_image(bbox, layer, tmp_path)
        tmp_path.replace(tif_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    return tif_path


def _read_raster_values(tif_path: Path):
    """Return a flat numpy array of valid (non-nodata) pixel values."""
    import numpy as np
    import rasterio  # lazy

    with rasterio.open(tif_path) as ds:
        arr = ds.read(1, masked=True)
    flat = arr.compressed()
    return np.asarray(flat)


# --------------------------------------------------------------------------- #
# Entropy / purity
# --------------------------------------------------------------------------- #


def shannon_entropy(probabilities) -> float:
    import numpy as np

    p = np.asarray([x for x in probabilities if x > 0], dtype=float)
    if p.size == 0:
        return 0.0
    return float(-(p * np.log(p)).sum())


def fuel_model_purity(class_distribution: dict[Any, float]) -> float:
    """1 - normalized Shannon entropy. 1.0 = single class, 0.0 = maximally mixed."""
    import numpy as np

    n = len(class_distribution)
    if n <= 1:
        return 1.0
    h = shannon_entropy(class_distribution.values())
    h_max = math.log(n)
    return float(max(0.0, min(1.0, 1.0 - h / h_max))) if h_max > 0 else 1.0


# --------------------------------------------------------------------------- #
# Public helpers — used by the terrain_fuel agent
# --------------------------------------------------------------------------- #


def get_fuel_model(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """FBFM40 class distribution, top-3 dominant classes, and purity score."""
    import numpy as np

    tif = _fetch_layer(bbox, FBFM40_LAYER)
    values = _read_raster_values(tif)
    total = int(values.size)
    if total == 0:
        return {
            "layer": FBFM40_LAYER,
            "pixels": 0,
            "class_distribution": {},
            "dominant_classes": [],
            "purity": 0.0,
            "tif_path": str(tif),
        }

    codes, counts = np.unique(values.astype(int), return_counts=True)
    dist: dict[str, float] = {}
    for code, cnt in zip(codes.tolist(), counts.tolist()):
        label = FBFM40_CODE_TO_LABEL.get(int(code), f"unknown ({int(code)})")
        key = f"{int(code)} {label}"
        dist[key] = float(cnt) / total

    purity = fuel_model_purity(dist)
    dominant = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "layer": FBFM40_LAYER,
        "pixels": total,
        "class_distribution": dist,
        "dominant_classes": [{"code": k, "fraction": v} for k, v in dominant],
        "purity": purity,
        "tif_path": str(tif),
    }


def _summarize_continuous(
    tif: Path,
    valid_range: tuple[float, float] | None = None,
) -> dict[str, float]:
    """Mean/percentile stats over the in-range pixels of a continuous raster.

    `valid_range=(lo, hi)` drops pixels outside [lo, hi]. ImageServer GeoTIFFs
    do not preserve nodata tags, so int16 sentinels (e.g. -32768) leak in and
    have to be filtered out by physical bounds — clamp at the caller.
    """
    import numpy as np

    values = _read_raster_values(tif).astype(float)
    if valid_range is not None:
        lo, hi = valid_range
        values = values[(values >= lo) & (values <= hi)]
    if values.size == 0:
        return {"mean": float("nan"), "p10": float("nan"), "p90": float("nan"),
                "min": float("nan"), "max": float("nan"), "pixels": 0}
    return {
        "mean": float(np.mean(values)),
        "p10": float(np.percentile(values, 10)),
        "p90": float(np.percentile(values, 90)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "pixels": int(values.size),
    }


def _aspect_distribution(tif: Path) -> dict[str, float]:
    """8-bin histogram (N, NE, E, SE, S, SW, W, NW) over 0-360 aspect degrees."""
    import numpy as np

    values = _read_raster_values(tif).astype(float)
    # LANDFIRE aspect: -1 = flat, 0-360 = azimuth. Anything else (e.g. the
    # int16 -32768 sentinel ImageServer leaves untagged) is nodata, not flat.
    flat = values[(values >= -1) & (values < 0)].size
    aspects = values[(values >= 0) & (values <= 360)]
    total = aspects.size + flat
    if total == 0:
        return {}

    bins = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    edges = [-22.5, 22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
    shifted = (aspects + 22.5) % 360 - 22.5
    out: dict[str, float] = {}
    for i, name in enumerate(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (shifted >= lo) & (shifted < hi)
        out[name] = float(mask.sum()) / total
    if flat:
        out["FLAT"] = float(flat) / total
    return out


def get_terrain(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Slope (deg), aspect distribution, and elevation stats from LANDFIRE."""
    slope_tif = _fetch_layer(bbox, SLOPE_DEG_LAYER)
    aspect_tif = _fetch_layer(bbox, ASPECT_LAYER)
    elev_tif = _fetch_layer(bbox, ELEVATION_LAYER)

    return {
        "slope_deg": _summarize_continuous(slope_tif, valid_range=(0.0, 90.0)),
        "aspect_distribution": _aspect_distribution(aspect_tif),
        "elevation_m": _summarize_continuous(elev_tif, valid_range=(-500.0, 9000.0)),
        "layers": {
            "slope": SLOPE_DEG_LAYER,
            "aspect": ASPECT_LAYER,
            "elevation": ELEVATION_LAYER,
        },
        "tif_paths": {
            "slope": str(slope_tif),
            "aspect": str(aspect_tif),
            "elevation": str(elev_tif),
        },
    }


def get_canopy_cover(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Canopy cover percentage stats + 5-bin distribution."""
    import numpy as np

    tif = _fetch_layer(bbox, CANOPY_COVER_LAYER)
    values = _read_raster_values(tif).astype(float)
    # LANDFIRE CC values 0-100 are real percent; >100 are non-burnable codes.
    cc = values[(values >= 0) & (values <= 100)]
    if cc.size == 0:
        return {
            "layer": CANOPY_COVER_LAYER,
            "pixels": 0,
            "mean_pct": float("nan"),
            "distribution": {},
            "tif_path": str(tif),
        }

    bins = [(0, 10, "open"), (10, 25, "low"), (25, 50, "moderate"),
            (50, 75, "high"), (75, 101, "closed")]
    dist: dict[str, float] = {}
    for lo, hi, name in bins:
        mask = (cc >= lo) & (cc < hi)
        dist[name] = float(mask.sum()) / cc.size

    return {
        "layer": CANOPY_COVER_LAYER,
        "pixels": int(cc.size),
        "mean_pct": float(np.mean(cc)),
        "p10_pct": float(np.percentile(cc, 10)),
        "p90_pct": float(np.percentile(cc, 90)),
        "distribution": dist,
        "tif_path": str(tif),
    }


# --------------------------------------------------------------------------- #
# Legacy stub kept for backwards-compat — older callers expected fetch_landfire
# --------------------------------------------------------------------------- #


async def fetch_landfire(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Convenience wrapper that fans the three blocking helpers off the event loop."""
    import asyncio

    fuel, terrain, canopy = await asyncio.gather(
        asyncio.to_thread(get_fuel_model, bbox),
        asyncio.to_thread(get_terrain, bbox),
        asyncio.to_thread(get_canopy_cover, bbox),
    )
    return {"fuel_model": fuel, "terrain": terrain, "canopy": canopy}
