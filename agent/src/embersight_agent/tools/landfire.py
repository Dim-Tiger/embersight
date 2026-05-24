"""LANDFIRE LFPS GP service wrapper (FBFM40 + slope + aspect + elevation + canopy).

Thin facade over the `landfire` PyPI package. Each public helper is a
synchronous function that returns a small JSON-shaped dict; callers in
async code should wrap them with `asyncio.to_thread` since the underlying
LANDFIRE polling job is blocking.

Rasters are cached under /tmp/landfire-cache/<bbox-hash>/<layer>.tif so
repeated runs in a dev session don't re-hit the public LFPS GP service.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
import zipfile
from pathlib import Path
from typing import Any

# LFPS v2 layer names — the legacy `<acronym>_<yr>` codes (e.g. 220F40_22) were
# retired with the May-2025 API cutover; current names look like `LFYYYY_<acro>`.
FBFM40_LAYER = "LF2022_FBFM40"
CANOPY_COVER_LAYER = "LF2022_CC"
SLOPE_DEG_LAYER = "LF2020_SlpD"
ASPECT_LAYER = "LF2020_Asp"
ELEVATION_LAYER = "LF2020_Elev"

LANDFIRE_VERSION = "LF 2022 (fuel/canopy) + LF 2020 (topographic)"

# v2 GP service endpoints. The old /arcgis/rest/services/.../submitJob path is
# intercepted by the LFPS Next.js frontend and never reaches ArcGIS — only the
# `/api/job/...` endpoints below return JSON.
LFPS_SUBMIT_URL = "https://lfps.usgs.gov/api/job/submit"
LFPS_STATUS_URL = "https://lfps.usgs.gov/api/job/status"
LFPS_USER_AGENT = "embersight-agent (+https://github.com/Dim-Tiger/embersight)"
LANDFIRE_SOURCE_URL = LFPS_SUBMIT_URL

CACHE_DIR = Path(os.environ.get("EMBERSIGHT_LANDFIRE_CACHE", "/tmp/landfire-cache"))

# v2 API requires a contact email per submission. Override with
# EMBERSIGHT_LANDFIRE_EMAIL when running outside dev.
_DEFAULT_EMAIL = "embersight-agent@example.com"

_POLL_INTERVAL_SEC = 5.0
_POLL_TIMEOUT_SEC = float(os.environ.get("EMBERSIGHT_LANDFIRE_TIMEOUT", "600"))

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


def _submit_job(bbox: tuple[float, float, float, float], layer: str, email: str) -> str:
    """POST a single-layer job to LFPS v2 and return the job id."""
    import requests  # lazy

    params = {
        "Email": email,
        "Layer_List": layer,
        "Area_of_Interest": _bbox_str(bbox),
        "Output_Projection": "4326",
    }
    headers = {"Accept": "application/json", "User-Agent": LFPS_USER_AGENT}
    r = requests.get(LFPS_SUBMIT_URL, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    body = r.json()
    job_id = body.get("jobId")
    if not job_id:
        raise RuntimeError(f"LFPS submit returned no jobId: {body}")
    return job_id


def _poll_job(job_id: str) -> str:
    """Poll LFPS v2 status until the job succeeds; return the outputFile URL."""
    import requests  # lazy

    headers = {"Accept": "application/json", "User-Agent": LFPS_USER_AGENT}
    deadline = time.monotonic() + _POLL_TIMEOUT_SEC
    last_status = "Pending"
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SEC)
        r = requests.get(
            LFPS_STATUS_URL, params={"JobId": job_id}, headers=headers, timeout=60
        )
        r.raise_for_status()
        body = r.json()
        last_status = body.get("status", "Unknown")
        if "Succeeded" in last_status:
            url = body.get("outputFile")
            if not url:
                raise RuntimeError(f"LFPS job {job_id} succeeded but had no outputFile")
            return url
        if "Failed" in last_status:
            msgs = [m.get("description", "") for m in body.get("messages", [])]
            err = next((m for m in msgs if "ERROR" in m), "; ".join(msgs[-3:]))
            raise RuntimeError(f"LFPS job {job_id} failed: {err}")
    raise TimeoutError(
        f"LFPS job {job_id} did not finish within {_POLL_TIMEOUT_SEC:.0f}s "
        f"(last status: {last_status})"
    )


def _download_zip(url: str, dest: Path) -> None:
    import requests  # lazy

    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as out:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    out.write(chunk)


def _fetch_layer(bbox: tuple[float, float, float, float], layer: str) -> Path:
    """Download a single LANDFIRE layer to the local cache; return the .tif path."""
    tif_path = _cache_path_for(bbox, layer)
    if tif_path.exists() and tif_path.stat().st_size > 0:
        return tif_path

    email = os.environ.get("EMBERSIGHT_LANDFIRE_EMAIL", _DEFAULT_EMAIL)
    job_id = _submit_job(bbox, layer, email)
    output_url = _poll_job(job_id)

    zip_path = tif_path.with_suffix(".zip")
    _download_zip(output_url, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        tif_members = [n for n in zf.namelist() if n.lower().endswith(".tif")]
        if not tif_members:
            raise RuntimeError(f"LANDFIRE response for {layer} contained no .tif")
        with zf.open(tif_members[0]) as src, open(tif_path, "wb") as dst:
            dst.write(src.read())

    try:
        zip_path.unlink()
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


def _summarize_continuous(tif: Path, nodata_max: float | None = None) -> dict[str, float]:
    import numpy as np

    values = _read_raster_values(tif).astype(float)
    if nodata_max is not None:
        values = values[values < nodata_max]
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
    # LANDFIRE aspect uses -1 for flat; clip out and count separately.
    flat = values[values < 0].size
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
        "slope_deg": _summarize_continuous(slope_tif),
        "aspect_distribution": _aspect_distribution(aspect_tif),
        "elevation_m": _summarize_continuous(elev_tif),
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
