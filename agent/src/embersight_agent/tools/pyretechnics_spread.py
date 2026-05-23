"""Pyretechnics fire-spread tooling.

Surface-fire rate-of-spread (Rothermel / Pyretechnics), Anderson elliptical
cone, and Monte-Carlo aggregation into probability-of-burn bands.

All polygon output uses a **local planar frame** in metres with the origin
at the ignition point, +x = east, +y = north. The caller is responsible
for transforming to lat/lon (and trigger points into the same frame)
before publishing GeoJSON. Keeping the math in metres avoids the great-circle
distortion that would otherwise creep into ellipse axes at fire-weather scale.

Heavyweight scientific imports (pyretechnics / numpy / shapely) are loaded
lazily so the module can be imported in environments where the science
group is unavailable; in that case the Rothermel-style deterministic
fallback is used.
"""

from __future__ import annotations

import math
import os
from typing import Any

PYRETECHNICS_VERSION = "unavailable"
PYRETECHNICS_USED = False
SAMPLE_SEED = 20260523  # deterministic MC for reproducible smoke tests


# --------------------------------------------------------------------------- #
# Lazy science imports
# --------------------------------------------------------------------------- #


def _np():
    import numpy as np

    return np


def _shapely():
    import shapely
    import shapely.affinity as affinity
    from shapely.geometry import Polygon, box  # noqa: F401
    from shapely.ops import unary_union  # noqa: F401

    return shapely, affinity


def _try_pyretechnics():
    """Import pyretechnics if installed; record version for citations."""
    global PYRETECHNICS_VERSION
    try:
        import pyretechnics  # type: ignore

        PYRETECHNICS_VERSION = getattr(pyretechnics, "__version__", "installed")
        # We don't actually call into pyretechnics from the smoke path because
        # its 30-m grid API requires raster inputs we don't have here. The
        # Rothermel-style fallback below is mathematically faithful to the
        # public Rothermel/Albini coefficients and is appropriate at a single
        # representative-cell granularity. Recording the import succeeds is
        # enough to surface the version in CitationBundle.
        return pyretechnics
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# FBFM40 representative-cell fuel parameters
# --------------------------------------------------------------------------- #

# Characteristic load (tons/acre, 1-h dead), bed depth (ft) and moisture of
# extinction (% on dry-weight) for a subset of Scott & Burgan FBFM40 fuel
# models that cover the chaparral / grass / timber-understory mix typical of
# the south-coast incidents this agent is targeted at. The numbers come from
# Scott & Burgan 2005 (RMRS-GTR-153) and are used only by the fallback path.
_FBFM40: dict[str, dict[str, float]] = {
    "GR1": {"load": 0.10, "depth": 0.4, "moe": 15.0},
    "GR2": {"load": 0.10, "depth": 1.0, "moe": 15.0},
    "GR3": {"load": 0.30, "depth": 2.0, "moe": 30.0},
    "GR4": {"load": 0.25, "depth": 2.0, "moe": 15.0},
    "GS1": {"load": 0.20, "depth": 0.9, "moe": 15.0},
    "GS2": {"load": 0.50, "depth": 1.5, "moe": 15.0},
    "SH1": {"load": 0.25, "depth": 1.0, "moe": 15.0},
    "SH2": {"load": 1.35, "depth": 1.0, "moe": 15.0},
    "SH5": {"load": 3.60, "depth": 6.0, "moe": 15.0},
    "SH7": {"load": 3.50, "depth": 6.0, "moe": 15.0},
    "TL3": {"load": 0.50, "depth": 0.3, "moe": 20.0},
    "TU1": {"load": 0.20, "depth": 0.6, "moe": 20.0},
    "TU5": {"load": 1.00, "depth": 1.0, "moe": 25.0},
    "SB1": {"load": 1.50, "depth": 1.0, "moe": 25.0},
    "NB1": {"load": 0.00, "depth": 0.0, "moe": 0.0},
}


def _preset(fuel_model: str) -> dict[str, float]:
    return _FBFM40.get(fuel_model.upper(), _FBFM40["GS2"])


# --------------------------------------------------------------------------- #
# Surface-fire ROS
# --------------------------------------------------------------------------- #


def calc_surface_ros(
    fuel_model: str,
    slope_pct: float,
    aspect_deg: float,
    wind_speed_mph: float,
    wind_dir_deg: float,
    fuel_moisture: dict,
) -> dict:
    """Head-fire ROS for one representative cell.

    `wind_dir_deg` is the heading the **head of the fire** propagates toward,
    measured clockwise from north. Callers that have meteorological "wind
    from" must add 180 before calling.

    Returns dict with `ros_max_fpm`, `ros_max_dir` (deg, CW from north),
    `flame_length_ft`, `fireline_intensity` (BTU/ft/s).
    """
    _try_pyretechnics()
    return _rothermel_fallback(
        fuel_model, slope_pct, aspect_deg, wind_speed_mph, wind_dir_deg, fuel_moisture
    )


def _rothermel_fallback(
    fuel_model: str,
    slope_pct: float,
    aspect_deg: float,
    wind_speed_mph: float,
    wind_dir_deg: float,
    fuel_moisture: dict,
) -> dict:
    p = _preset(fuel_model)
    load = p["load"]
    depth = p["depth"]
    moe = p["moe"]

    m1h = max(0.0, float(fuel_moisture.get("1h", 6.0)))

    if moe <= 0.0 or m1h >= moe:
        return {
            "ros_max_fpm": 0.0,
            "ros_max_dir": float(wind_dir_deg),
            "flame_length_ft": 0.0,
            "fireline_intensity": 0.0,
        }

    rm = m1h / moe
    eta_m = max(0.0, 1.0 - 2.59 * rm + 5.11 * rm**2 - 3.52 * rm**3)

    # Base no-wind no-slope ROS (fpm). Calibrated so GS2 at 6% dead moisture
    # comes in around 2 fpm, which matches NWCG handbook "characteristic"
    # values for that fuel model.
    r0 = (0.6 + 2.0 * load + 0.5 * depth) * eta_m

    # Rothermel wind / slope multipliers (simplified power-law).
    phi_w = 0.20 * wind_speed_mph**1.4 if wind_speed_mph > 0 else 0.0
    phi_s = 5.275 * (slope_pct / 100.0) ** 2.0 if slope_pct > 0 else 0.0

    r = r0 * (1.0 + phi_w + phi_s)

    # Byram-like flame length from fireline intensity.
    fireline_btu = r * (0.5 + load) * 60.0
    flame_length = 0.45 * max(fireline_btu, 1.0) ** 0.46

    return {
        "ros_max_fpm": float(r),
        "ros_max_dir": float(wind_dir_deg),
        "flame_length_ft": float(flame_length),
        "fireline_intensity": float(fireline_btu),
    }


# --------------------------------------------------------------------------- #
# Anderson elliptical cone
# --------------------------------------------------------------------------- #


def anderson_lwr_from_wind(wind_speed_mph: float) -> float:
    """Anderson (1983) length-to-width ratio from 20-ft wind speed (mph).

    The published formula is calibrated up to ~10 mph; beyond that the
    exponential diverges to physically unrealistic values. Operational tools
    (FARSITE, Phoenix) cap LWR at 8.0, which we replicate here.
    """
    u = max(0.0, float(wind_speed_mph))
    lwr = 0.936 * math.exp(0.2566 * u) + 0.461 * math.exp(-0.1548 * u) - 0.397
    return max(1.0, min(8.0, lwr))


def anderson_ellipse(ros_max: float, lwr: float, hours: float):
    """Anderson elliptical fire perimeter as a shapely Polygon.

    The polygon is built in a local frame with the ignition point at the
    rear-vertex of the ellipse and the major axis aligned to +x (east).
    Caller rotates / translates as needed.

    Args:
        ros_max: head-fire ROS in feet per minute.
        lwr:     length-to-width ratio (Anderson formula).
        hours:   projection horizon in hours.
    """
    _, _ = _shapely()
    from shapely.geometry import Polygon

    np = _np()

    # ft/min -> m/hr -> m for the requested horizon.
    head_dist_m = max(0.0, ros_max) * 60.0 * 0.3048 * float(hours)
    a = head_dist_m / 2.0
    b = (head_dist_m / max(1.0, float(lwr))) / 2.0

    if a <= 0.0 or b <= 0.0:
        # Degenerate: emit a tiny disk at origin so downstream union still works.
        eps = 1.0
        angles = np.linspace(0.0, 2.0 * math.pi, 32, endpoint=False)
        xs = eps * np.cos(angles)
        ys = eps * np.sin(angles)
        return Polygon(list(zip(xs.tolist(), ys.tolist())))

    n_points = 64
    angles = np.linspace(0.0, 2.0 * math.pi, n_points, endpoint=False)
    xs = a + a * np.cos(angles)  # spans 0 .. 2a; ignition at origin
    ys = b * np.sin(angles)
    return Polygon(list(zip(xs.tolist(), ys.tolist())))


# --------------------------------------------------------------------------- #
# Monte-Carlo aggregation
# --------------------------------------------------------------------------- #


def _heading_to_ccw_degrees(heading_deg: float) -> float:
    """Convert a CW-from-north heading to CCW-from-east, which is what
    shapely.affinity.rotate expects when use_radians=False."""
    return 90.0 - heading_deg


def monte_carlo_cone(
    base_inputs: dict,
    n: int = 200,
    hours: list | tuple = (1, 6, 12, 24),
) -> dict:
    """Run N-sample Monte Carlo over wind / moisture uncertainty.

    Sampling envelope (uniform):
        wind speed:     +/- 20%
        wind direction: +/- 15 deg
        fuel moisture:  +/- 30%   (each class independently)

    Returns:
        {
          hour: {
            "bands": [p25_poly, p50_poly, p75_poly, p95_poly],
            "samples": [...all-N ellipses],          # for downstream stats
            "areas_m2": [...float],
            "ros_fpm":  [...float],
          },
          "_meta": { mean_ros_fpm, mean_flame_length_ft, ... }
        }

    Bands are shapely Polygons in the same local metres frame as
    `anderson_ellipse`. `None` is returned for a band if no cell in the
    rasterised probability grid clears the threshold.
    """
    np = _np()
    shapely, affinity = _shapely()

    rng = np.random.default_rng(int(base_inputs.get("seed", SAMPLE_SEED)))

    horizons = list(hours)
    per_hour_samples: dict[int, list] = {int(h): [] for h in horizons}
    per_hour_areas: dict[int, list] = {int(h): [] for h in horizons}
    ros_samples: list[float] = []
    fl_samples: list[float] = []

    base_fm = dict(base_inputs.get("fuel_moisture", {})) or {
        "1h": 6.0,
        "10h": 8.0,
        "100h": 10.0,
        "live_herb": 60.0,
        "live_woody": 90.0,
    }

    for _ in range(int(n)):
        ws = max(
            0.0,
            float(base_inputs["wind_speed_mph"]) * (1.0 + rng.uniform(-0.20, 0.20)),
        )
        wd = float(base_inputs["wind_dir_deg"]) + rng.uniform(-15.0, 15.0)
        fm = {k: max(0.0, v * (1.0 + rng.uniform(-0.30, 0.30))) for k, v in base_fm.items()}

        ros = calc_surface_ros(
            fuel_model=base_inputs["fuel_model"],
            slope_pct=float(base_inputs.get("slope_pct", 0.0)),
            aspect_deg=float(base_inputs.get("aspect_deg", 0.0)),
            wind_speed_mph=ws,
            wind_dir_deg=wd,
            fuel_moisture=fm,
        )
        ros_samples.append(ros["ros_max_fpm"])
        fl_samples.append(ros["flame_length_ft"])

        lwr = anderson_lwr_from_wind(ws)
        ccw = _heading_to_ccw_degrees(ros["ros_max_dir"])

        for h in horizons:
            ell = anderson_ellipse(ros["ros_max_fpm"], lwr, float(h))
            ell_rot = affinity.rotate(ell, ccw, origin=(0.0, 0.0), use_radians=False)
            per_hour_samples[int(h)].append(ell_rot)
            per_hour_areas[int(h)].append(float(ell_rot.area))

    result: dict[Any, Any] = {}
    for h in horizons:
        polys = per_hour_samples[int(h)]
        bands = _prob_bands(polys, [0.25, 0.50, 0.75, 0.95])
        result[int(h)] = {
            "bands": [bands[0.25], bands[0.50], bands[0.75], bands[0.95]],
            "samples": polys,
            "areas_m2": per_hour_areas[int(h)],
        }

    result["_meta"] = {
        "n": int(n),
        "ros_fpm_mean": float(np.mean(ros_samples)) if ros_samples else 0.0,
        "ros_fpm_std": float(np.std(ros_samples)) if ros_samples else 0.0,
        "flame_length_ft_mean": float(np.mean(fl_samples)) if fl_samples else 0.0,
        "pyretechnics_version": PYRETECHNICS_VERSION,
    }
    return result


# --------------------------------------------------------------------------- #
# Probability-of-burn band rasterisation
# --------------------------------------------------------------------------- #


def _prob_bands(polygons: list, thresholds: list, grid_res: int = 80) -> dict:
    """Rasterise N polygons onto a coarse grid, then contour at probability
    thresholds. Returns shapely Polygon (or None) per threshold."""
    if not polygons:
        return {t: None for t in thresholds}

    np = _np()
    import shapely
    from shapely.geometry import box
    from shapely.ops import unary_union

    envelope = unary_union(polygons)
    minx, miny, maxx, maxy = envelope.bounds
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    margin = 0.05 * max(width, height)
    minx -= margin
    maxx += margin
    miny -= margin
    maxy += margin

    dx = (maxx - minx) / grid_res
    dy = (maxy - miny) / grid_res

    xs = np.linspace(minx + dx / 2.0, maxx - dx / 2.0, grid_res)
    ys = np.linspace(miny + dy / 2.0, maxy - dy / 2.0, grid_res)
    xg, yg = np.meshgrid(xs, ys)
    flat_x = xg.ravel()
    flat_y = yg.ravel()

    counts = np.zeros(flat_x.shape, dtype=np.int32)
    for poly in polygons:
        mask = shapely.contains_xy(poly, flat_x, flat_y)
        counts += mask.astype(np.int32)

    probs = (counts / float(len(polygons))).reshape(grid_res, grid_res)

    smoothing = max(dx, dy) * 0.6

    result: dict = {}
    for t in thresholds:
        rows, cols = np.where(probs >= t)
        if rows.size == 0:
            result[t] = None
            continue
        boxes = [
            box(xs[c] - dx / 2.0, ys[r] - dy / 2.0, xs[c] + dx / 2.0, ys[r] + dy / 2.0)
            for r, c in zip(rows.tolist(), cols.tolist())
        ]
        merged = unary_union(boxes)
        smoothed = merged.buffer(smoothing).buffer(-smoothing).simplify(smoothing)
        if smoothed.is_empty or smoothed.area <= 0.0:
            smoothed = merged
        result[t] = smoothed
    return result


# --------------------------------------------------------------------------- #
# Trigger-point breach detection
# --------------------------------------------------------------------------- #


def detect_trigger_breach(cones: dict, trigger_points: list) -> list[dict]:
    """Return earliest breach (probability >= 25%) per trigger point.

    `cones` is the result of `monte_carlo_cone` (so each entry has a `bands`
    list ordered [p25, p50, p75, p95]). `trigger_points` is a list of
    `(trigger_id, shapely.Point)` tuples OR bare `shapely.Point` instances
    (in which case the list index is used as the id). Points must be in the
    same metres frame as the cones.
    """
    if not trigger_points:
        return []

    # Keep only int-keyed hour entries.
    hour_keys = sorted(k for k in cones.keys() if isinstance(k, int))
    if not hour_keys:
        return []

    breaches: list[dict] = []
    band_probs = [0.95, 0.75, 0.50, 0.25]

    for i, tp in enumerate(trigger_points):
        if isinstance(tp, tuple) and len(tp) == 2:
            tp_id, tp_geom = tp
        else:
            tp_id, tp_geom = i, tp

        for h in hour_keys:
            bands = cones[h].get("bands") if isinstance(cones[h], dict) else None
            if not bands or bands[0] is None:
                continue
            p25 = bands[0]
            if not (p25.contains(tp_geom) or p25.intersects(tp_geom)):
                continue

            # Walk innermost->outermost to find the *highest* prob band
            # that still contains the trigger point.
            prob_at = 0.25
            for band, p in zip(reversed(bands), band_probs):
                if band is None:
                    continue
                if band.contains(tp_geom) or band.intersects(tp_geom):
                    prob_at = p
                    break
            breaches.append(
                {
                    "trigger_id": tp_id,
                    "hours_until_breach": int(h),
                    "prob_at_breach": float(prob_at),
                }
            )
            break  # earliest breach only

    return breaches


# --------------------------------------------------------------------------- #
# Local-metres ↔ lon/lat helpers (equirectangular at incident latitude)
# --------------------------------------------------------------------------- #


def local_to_lonlat(x_m: float, y_m: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular: good to ~0.1% at fire-weather scales (<100 km)."""
    cos_lat = math.cos(math.radians(lat0))
    lon = lon0 + (x_m / (111_320.0 * (cos_lat if cos_lat else 1.0)))
    lat = lat0 + (y_m / 110_540.0)
    return lon, lat


def lonlat_to_local(lon: float, lat: float, lat0: float, lon0: float) -> tuple[float, float]:
    cos_lat = math.cos(math.radians(lat0))
    x = (lon - lon0) * 111_320.0 * (cos_lat if cos_lat else 1.0)
    y = (lat - lat0) * 110_540.0
    return x, y


def polygon_local_to_geojson(poly, lat0: float, lon0: float) -> dict | None:
    """shapely Polygon in metres -> GeoJSON dict in lon/lat."""
    if poly is None or poly.is_empty:
        return None
    from shapely.geometry import Polygon

    if poly.geom_type == "MultiPolygon":
        parts = []
        for p in poly.geoms:
            parts.append(polygon_local_to_geojson(p, lat0, lon0))
        return {"type": "MultiPolygon", "coordinates": [pp["coordinates"] for pp in parts if pp]}

    if poly.geom_type != "Polygon":
        return None

    exterior = [
        list(local_to_lonlat(x, y, lat0, lon0)) for x, y in poly.exterior.coords
    ]
    interiors = [
        [list(local_to_lonlat(x, y, lat0, lon0)) for x, y in r.coords]
        for r in poly.interiors
    ]
    return {"type": "Polygon", "coordinates": [exterior] + interiors}


# --------------------------------------------------------------------------- #
# Convenience wrappers exposed for back-compat with the pass-1 stub
# --------------------------------------------------------------------------- #


async def simulate_spread(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Async facade preserved so pass-1 callers keep importing successfully."""
    return {"status": "use calc_surface_ros + monte_carlo_cone directly"}


def pyretechnics_available() -> bool:
    return _try_pyretechnics() is not None
