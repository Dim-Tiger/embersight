"""HRRR / RTMA gridded weather via Herbie.

Pulls 10-m winds, 2-m temperature, and 2-m relative humidity at a point by
locating the nearest grid cell from each forecast hour. Designed to be safe
to call from a LangGraph node:

* heavy `herbie-data` import is deferred to call-time
* GRIBs are cached under `/tmp/herbie-cache/` so reruns are cheap
* network / data failures degrade to an empty hourly series rather than
  raising — the agent treats missing HRRR/RTMA as a confidence penalty
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CACHE_DIR = Path(os.environ.get("HERBIE_CACHE_DIR", "/tmp/herbie-cache"))

# Variables we extract from each GRIB. The keys are the canonical names the
# rest of the agent uses; the values are the regex Herbie/cfgrib match against
# the GRIB inventory line.
_HRRR_SEARCH = "(?:UGRD|VGRD):10 m above ground|(?:TMP|RH):2 m above ground"
_RTMA_SEARCH = _HRRR_SEARCH


def _latest_hrrr_run(now: datetime | None = None) -> datetime:
    """Round to the most recent HRRR cycle (every hour, ~1h latency).

    Returns a naive (tz-stripped) UTC datetime — Herbie/pandas internals
    raise on tz-aware values.
    """
    now = now or datetime.now(timezone.utc)
    # Give the upstream a 90-minute buffer to publish the cycle.
    candidate = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    return candidate.replace(tzinfo=None)


def _latest_rtma_run(now: datetime | None = None) -> datetime:
    """RTMA is hourly with shorter latency than HRRR."""
    now = now or datetime.now(timezone.utc)
    return (now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)).replace(tzinfo=None)


def _nearest_point_values(ds: Any, lat: float, lon: float) -> dict[str, float | None]:
    """Pull each surface variable at the nearest grid cell.

    HRRR uses Lambert Conformal grids with `latitude`/`longitude` 2-D arrays
    and integer `y`/`x` coordinates. We compute great-circle distance against
    every grid cell and take the argmin — slow on a continental grid but
    bulletproof across model/projection variants.
    """
    import numpy as np

    lats = ds["latitude"].values
    lons = ds["longitude"].values
    # Normalise longitude to (-180, 180] so the diff math is well-behaved.
    lons_norm = ((lons + 180.0) % 360.0) - 180.0

    dlat = np.radians(lats - lat)
    dlon = np.radians(lons_norm - lon)
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(math.radians(lat)) * np.cos(np.radians(lats)) * np.sin(dlon / 2.0) ** 2
    )
    dist = 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    idx = np.unravel_index(int(np.argmin(dist)), dist.shape)

    out: dict[str, float | None] = {}
    rename = {
        "u10": "wind_u_ms",
        "v10": "wind_v_ms",
        "t2m": "temp_k",
        "r2": "rh_pct",
        "2t": "temp_k",
        "2r": "rh_pct",
        "10u": "wind_u_ms",
        "10v": "wind_v_ms",
    }
    for var in ds.data_vars:
        canonical = rename.get(str(var))
        if canonical is None:
            continue
        try:
            val = float(ds[var].values[idx])
        except Exception:  # noqa: BLE001
            val = None
        out[canonical] = val
    return out


def _derive(values: dict[str, float | None]) -> dict[str, float | None]:
    """Convert raw GRIB units into the canonical FBAN-facing units.

    * wind from u/v → speed (mph) + direction (deg, met. convention)
    * temp from K → F
    * RH already in %
    """
    u = values.get("wind_u_ms")
    v = values.get("wind_v_ms")
    t_k = values.get("temp_k")
    rh = values.get("rh_pct")

    out: dict[str, float | None] = {}
    if u is not None and v is not None:
        speed_ms = math.hypot(u, v)
        out["wind_speed_mph"] = round(speed_ms * 2.2369362921, 2)
        # Meteorological wind direction = direction wind is FROM.
        direction = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
        out["wind_direction_deg"] = round(direction, 1)
    else:
        out["wind_speed_mph"] = None
        out["wind_direction_deg"] = None

    out["temp_f"] = round((t_k - 273.15) * 9.0 / 5.0 + 32.0, 2) if t_k is not None else None
    out["rh_pct"] = round(rh, 1) if rh is not None else None
    return out


def get_hrrr_forecast(lat: float, lon: float, hours: int = 24) -> dict[str, Any]:
    """Return an hourly HRRR forecast (wind/temp/RH) at the nearest grid cell.

    Returns a dict with `run`, `lat`, `lon`, and `hourly` (list of dicts with
    `valid_time`, `fxx`, and derived fields). Missing hours are returned with
    null fields; an `error` key is set when the whole pull fails.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from herbie import Herbie  # heavy import; defer
    except Exception as exc:  # noqa: BLE001
        return {
            "run": None,
            "lat": lat,
            "lon": lon,
            "hourly": [],
            "error": f"herbie unavailable: {exc}",
        }

    run = _latest_hrrr_run()
    hourly: list[dict[str, Any]] = []
    failures = 0

    for fxx in range(0, hours + 1):
        try:
            h = Herbie(
                run,
                model="hrrr",
                product="sfc",
                fxx=fxx,
                save_dir=CACHE_DIR,
                verbose=False,
            )
            ds = h.xarray(_HRRR_SEARCH, remove_grib=False)
        except Exception as exc:  # noqa: BLE001
            hourly.append(
                {
                    "valid_time": (run + timedelta(hours=fxx)).isoformat(),
                    "fxx": fxx,
                    "error": f"{type(exc).__name__}: {exc}",
                    "wind_speed_mph": None,
                    "wind_direction_deg": None,
                    "temp_f": None,
                    "rh_pct": None,
                }
            )
            failures += 1
            continue

        # Herbie returns either a single Dataset or a list of Datasets when
        # multiple GRIB messages match the search regex.
        ds_list = ds if isinstance(ds, list) else [ds]
        merged: dict[str, float | None] = {}
        for one in ds_list:
            merged.update(_nearest_point_values(one, lat, lon))
        derived = _derive(merged)
        hourly.append(
            {
                "valid_time": (run + timedelta(hours=fxx)).isoformat(),
                "fxx": fxx,
                **derived,
            }
        )

    result: dict[str, Any] = {
        "run": run.isoformat(),
        "lat": lat,
        "lon": lon,
        "hourly": hourly,
        "model": "HRRR",
    }
    if failures and failures >= hours:
        result["error"] = "all forecast hours failed to pull"
    return result


def get_rtma_analysis(lat: float, lon: float) -> dict[str, Any]:
    """Return the most recent RTMA analysis at the nearest grid cell."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from herbie import Herbie
    except Exception as exc:  # noqa: BLE001
        return {
            "run": None,
            "lat": lat,
            "lon": lon,
            "error": f"herbie unavailable: {exc}",
        }

    run = _latest_rtma_run()
    try:
        h = Herbie(
            run,
            model="rtma",
            product="anl",
            fxx=0,
            save_dir=CACHE_DIR,
            verbose=False,
        )
        ds = h.xarray(_RTMA_SEARCH, remove_grib=False)
    except Exception as exc:  # noqa: BLE001
        return {
            "run": run.isoformat(),
            "lat": lat,
            "lon": lon,
            "error": f"{type(exc).__name__}: {exc}",
            "wind_speed_mph": None,
            "wind_direction_deg": None,
            "temp_f": None,
            "rh_pct": None,
        }

    ds_list = ds if isinstance(ds, list) else [ds]
    merged: dict[str, float | None] = {}
    for one in ds_list:
        merged.update(_nearest_point_values(one, lat, lon))
    derived = _derive(merged)
    return {
        "run": run.isoformat(),
        "valid_time": run.isoformat(),
        "lat": lat,
        "lon": lon,
        "model": "RTMA",
        **derived,
    }


# --------------------------------------------------------------------------- #
# Back-compat shim
# --------------------------------------------------------------------------- #


async def fetch_hrrr_forecast(lat: float, lon: float, fxx: int = 6) -> dict[str, Any]:
    """Legacy async entrypoint; pulls a single forecast hour."""
    import asyncio

    full = await asyncio.to_thread(get_hrrr_forecast, lat, lon, fxx)
    if full.get("hourly"):
        target = next(
            (h for h in full["hourly"] if h.get("fxx") == fxx),
            full["hourly"][-1],
        )
        return {"run": full.get("run"), **target}
    return full


if __name__ == "__main__":
    print("HRRR pull (3-hour smoke test)...")
    hrrr = get_hrrr_forecast(34.7402, -119.3142, hours=2)
    print(
        f"  run={hrrr.get('run')} hours={len(hrrr.get('hourly', []))} "
        f"error={hrrr.get('error', 'none')}"
    )
    for entry in hrrr.get("hourly", [])[:3]:
        print(f"  fxx={entry['fxx']}: {entry}")
    print("RTMA nowcast...")
    rtma = get_rtma_analysis(34.7402, -119.3142)
    print(f"  {rtma}")
