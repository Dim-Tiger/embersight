"""Turn Camarillo's SB 99 map (Safety Element Figure 11-14) into GeoJSON.

The Safety Element (Sept 2024, p. 11-42) says Rincon found 37 single-access
neighborhoods and 7 CAL FIRE subdivisions, but Figure 11-14 (p. 11-43) is a
picture with no names or coordinates. This script reads that picture and
places each marker on the real map, so EmberSight's no-fire single-exit
areas can be checked against the city's own list.

Steps:
  1. pull the map raster out of the PDF (page 11-43)
  2. find the black "Single-Access Point" stars by matching the legend star
  3. georeference the raster: start from hand-read control intersections,
     then nudge the fit so OSM roads land on the roads drawn in the picture
  4. trace the cyan neighborhoods and the purple / blue CAL FIRE outlines
  5. write GeoJSON plus an overlay PNG to check the fit by eye

Usage (Python 3.11+ with pymupdf, numpy, scipy, pillow):
  python build_reference.py --pdf camarillo-safety-element.pdf --roads roads.json

roads.json is one Overpass response (`out tags geom`) for highway ways in
bbox 34.16,-119.13,34.28,-118.94. See README.md for the exact query.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw
from scipy import ndimage, optimize, signal

HERE = Path(__file__).resolve().parent
R_EARTH = 6378137.0  # Web Mercator sphere radius, metres

# Pixel boxes are in raster pixels (x right, y down) of the 1752 x 1261 map image.
LEGEND_BOX = (0, 790, 620, 1261)  # x0, y0, x1, y1; the legend has its own star

# Hand-read control points: (pixel x, pixel y) -> (lon, lat) of the OSM
# intersection node. Read from 4x gridded close-ups of the raster.
CONTROL_POINTS = [
    ("Bradley Rd / E Los Angeles Ave (SR-118)", (1097, 132), (-119.01122, 34.26376)),
    ("La Vista Ave / W Los Angeles Ave (SR-118)", (302, 126), (-119.09374, 34.26365)),
    ("Arneill Rd / E Ponderosa Dr", (828, 593), (-119.03899, 34.22357)),
]

# Stars the template matcher cannot separate: three stars overlap in a tight
# knot near the east end of Santa Rosa Rd. Placed by eye on a 12x zoom.
MANUAL_STARS = [(1412, 540), (1404, 548), (1399, 561)]

STAR_SCORE_MIN = 0.50  # normalised correlation with the legend star


# --------------------------------------------------------------------------
# Projection helpers
# --------------------------------------------------------------------------

def to_merc(lon: float, lat: float) -> tuple[float, float]:
    return (R_EARTH * math.radians(lon),
            R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def from_merc(x: float, y: float) -> tuple[float, float]:
    return (math.degrees(x / R_EARTH),
            math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2))


class Affine:
    """pixel = [mx, my, 1] @ M, where M is a 3x2 matrix in Web Mercator."""

    def __init__(self, M: np.ndarray):
        self.M = M

    @classmethod
    def fit(cls, merc_pts: np.ndarray, px_pts: np.ndarray) -> "Affine":
        A = np.column_stack([merc_pts, np.ones(len(merc_pts))])
        return cls(np.linalg.lstsq(A, px_pts, rcond=None)[0])

    def to_px(self, merc_pts: np.ndarray) -> np.ndarray:
        return np.column_stack([merc_pts, np.ones(len(merc_pts))]) @ self.M

    def to_lonlat(self, px: float, py: float) -> tuple[float, float]:
        # Invert the 2x2 part, then undo the offset.
        lin, off = self.M[:2], self.M[2]
        mx, my = np.linalg.solve(lin.T, np.array([px, py]) - off)
        return from_merc(mx, my)


# --------------------------------------------------------------------------
# 1. Raster
# --------------------------------------------------------------------------

def extract_map(pdf_path: Path) -> Image.Image:
    """Largest image on PDF page 47 (printed page 11-43) is the map."""
    doc = pymupdf.open(pdf_path)
    page = doc[46]
    best = max(page.get_images(full=True), key=lambda im: im[2] * im[3])
    pix = pymupdf.Pixmap(doc, best[0])
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


# --------------------------------------------------------------------------
# 2. Stars
# --------------------------------------------------------------------------

def in_box(x, y, box):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def find_stars(rgb: np.ndarray) -> list[dict]:
    dark = (rgb.max(axis=2) < 70).astype(float)
    tmpl = dark[881:896, 39:54]  # the legend's own star symbol
    tz = tmpl - tmpl.mean()
    corr = signal.correlate(dark - dark.mean(), tz, mode="same")
    energy = signal.correlate(dark ** 2, np.ones_like(tmpl), mode="same")
    score = corr / (np.sqrt(np.clip(energy, 0, None)) * np.sqrt((tz ** 2).sum()) + 1e-9)
    peaks = (score == ndimage.maximum_filter(score, size=7)) & (score > STAR_SCORE_MIN)
    h, w = dark.shape
    stars = []
    for y, x in np.argwhere(peaks):
        if in_box(x, y, LEGEND_BOX) or x < 6 or y < 6 or x > w - 6 or y > h - 6:
            continue
        stars.append({"px": int(x), "py": int(y), "score": round(float(score[y, x]), 2),
                      "found_by": "template"})
    for x, y in MANUAL_STARS:
        stars.append({"px": x, "py": y, "score": None, "found_by": "manual"})
    # West to east, so ids read left to right across the map.
    stars.sort(key=lambda s: (s["px"], s["py"]))
    for i, s in enumerate(stars, 1):
        s["id"] = f"CAM-SA-{i:02d}"
    return stars


# --------------------------------------------------------------------------
# 3. Georeference
# --------------------------------------------------------------------------

def load_roads(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    roads = []
    for el in data["elements"]:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        pts = np.array([to_merc(g["lon"], g["lat"]) for g in el["geometry"]])
        roads.append({"name": el.get("tags", {}).get("name"),
                      "highway": el.get("tags", {}).get("highway"), "merc": pts})
    return roads


def densify(pts: np.ndarray, step: float) -> np.ndarray:
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.linalg.norm(b - a) / step))
        out.extend(a + (b - a) * (k / n) for k in range(1, n + 1))
    return np.array(out)


def line_mask(rgb: np.ndarray) -> np.ndarray:
    """Pixels that look like drawn road lines: grey on white or white on grey."""
    grey = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    local = ndimage.median_filter(grey, size=15)
    return (np.abs(grey - local) > 10) & (sat < 25)


def refine(aff: Affine, rgb: np.ndarray, roads: list[dict]) -> tuple[Affine, float, float]:
    """Nudge the affine so OSM road vertices sit on drawn road lines.

    Chamfer matching: the distance transform gives, for every pixel, how far
    it is to the nearest drawn line; we minimise the (capped) mean of that
    distance over densified OSM road vertices.
    """
    dist = ndimage.distance_transform_edt(~line_mask(rgb))
    keep = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential"}
    verts = np.vstack([densify(r["merc"], 25.0) for r in roads if r["highway"] in keep])
    h, w = dist.shape

    def cost(flat):
        px = Affine(flat.reshape(3, 2)).to_px(verts)
        ok = (px[:, 0] >= 0) & (px[:, 0] < w - 1) & (px[:, 1] >= 0) & (px[:, 1] < h - 1)
        d = ndimage.map_coordinates(dist, [px[ok, 1], px[ok, 0]], order=1)
        return np.minimum(d, 8.0).mean()

    before = cost(aff.M.ravel())
    # Scale the search so each parameter moves in "pixel-sized" units.
    x0 = aff.M.ravel()
    scale = np.abs(x0) * 1e-3 + 1e-6
    res = optimize.minimize(lambda z: cost(x0 + z * scale), np.zeros(6), method="Powell",
                            options={"xtol": 1e-3, "ftol": 1e-4, "maxiter": 4000})
    best = Affine((x0 + res.x * scale).reshape(3, 2))
    return best, before, cost(best.M.ravel())


def metres_per_px(aff: Affine, lat: float) -> float:
    lin = aff.M[:2]
    merc_per_px = 1 / math.sqrt(abs(np.linalg.det(lin)))
    return merc_per_px * math.cos(math.radians(lat))  # Web Mercator -> ground metres


# --------------------------------------------------------------------------
# 4. Polygons
# --------------------------------------------------------------------------

def components(mask: np.ndarray, min_px: int, grow: int) -> list[np.ndarray]:
    """Connected blobs of one colour, with small gaps bridged.

    Outlines on the map are 1-2 px lines broken wherever another layer is
    drawn on top, so we grow the mask a little before labelling and then
    fill the inside. Outlines that stay broken come out as fragments.
    """
    grown = ndimage.binary_dilation(mask, iterations=grow) if grow else mask
    lab, n = ndimage.label(ndimage.binary_fill_holes(grown))
    out = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(xs) >= min_px:
            out.append(np.column_stack([xs, ys]))
    return out


def hull(points: np.ndarray) -> np.ndarray:
    """Convex hull (monotone chain); rough, but honest about being rough."""
    pts = sorted(set(map(tuple, points.tolist())))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def colour_masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = (rgb[..., k].astype(int) for k in range(3))
    h, w = r.shape
    yy, xx = np.mgrid[:h, :w]
    not_legend = ~((xx >= LEGEND_BOX[0]) & (xx < LEGEND_BOX[2]) &
                   (yy >= LEGEND_BOX[1]) & (yy < LEGEND_BOX[3]))
    return {
        # "Identified Single Access Neighborhood": cyan fill and outline
        "cyan": (g - r > 25) & (b - r > 15) & (g > 150) & not_legend,
        # "Approved by CAL FIRE": purple outline
        "purple": (r > 100) & (b > 150) & (g < 90) & not_legend,
        # "In Review by CAL FIRE": blue outline
        "blue": (b > 180) & (r < 90) & (g > 60) & (g < 170) & not_legend,
    }


# --------------------------------------------------------------------------
# 5. Output
# --------------------------------------------------------------------------

def nearest_street(lon, lat, roads):
    p = np.array(to_merc(lon, lat))
    best, best_d = None, float("inf")
    k = math.cos(math.radians(lat))
    for r in roads:
        if not r["name"]:
            continue
        pts = r["merc"]
        a, b = pts[:-1], pts[1:]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
        d = np.linalg.norm(a + ab * t[:, None] - p, axis=1).min() * k
        if d < best_d:
            best, best_d = r["name"], d
    return best, round(best_d)


def feature(geom, props):
    return {"type": "Feature", "geometry": geom, "properties": props}


def write_fc(path: Path, features, note: str):
    fc = {"type": "FeatureCollection", "name": path.stem,
          "note": note, "features": features}
    path.write_text(json.dumps(fc, indent=1) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--roads", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()

    img = extract_map(args.pdf)
    rgb = np.asarray(img).astype(int)
    roads = load_roads(args.roads)

    # Georeference
    merc = np.array([to_merc(*ll) for _, _, ll in CONTROL_POINTS])
    px = np.array([p for _, p, _ in CONTROL_POINTS], float)
    aff0 = Affine.fit(merc, px)
    aff, cost0, cost1 = refine(aff0, rgb, roads)
    mpp = metres_per_px(aff, 34.24)
    resid = np.linalg.norm(aff.to_px(merc) - px, axis=1) * mpp
    rms = float(np.sqrt((resid ** 2).mean()))
    print(f"scale {mpp:.2f} m/px; chamfer mean {cost0:.2f} -> {cost1:.2f} px; "
          f"control-point RMS {rms:.0f} m ({', '.join(f'{r:.0f}' for r in resid)})")

    # Stars
    stars = find_stars(rgb)
    feats = []
    for s in stars:
        lon, lat = aff.to_lonlat(s["px"], s["py"])
        street, d = nearest_street(lon, lat, roads)
        conf = "high" if (s["score"] or 0) >= 0.6 else "medium"
        if s["found_by"] == "manual":
            conf = "medium (overlapping stars, placed by eye)"
        feats.append(feature({"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}, {
            "id": s["id"], "pixel_x": s["px"], "pixel_y": s["py"],
            "match_score": s["score"], "found_by": s["found_by"], "confidence": conf,
            "nearest_osm_street": street, "nearest_osm_street_m": d}))
    write_fc(args.out / "single_access_points.geojson", feats,
             "Approximate. Stars from Camarillo Safety Element Fig. 11-14, georeferenced by "
             f"this script (control-point RMS about {rms:.0f} m).")
    print(f"{len(stars)} stars")

    # Polygons
    masks = colour_masks(rgb)
    layers = {"cyan": [], "purple": [], "blue": []}
    # Cyan areas are filled, so they need no growing; outlines need a bridge.
    for key, min_px, grow in (("cyan", 60, 0), ("purple", 150, 2), ("blue", 150, 2)):
        for comp in components(masks[key], min_px, grow):
            ring = hull(comp)
            ll = [list(map(lambda v: round(v, 6), aff.to_lonlat(x, y))) for x, y in ring]
            ll.append(ll[0])
            cx, cy = comp.mean(axis=0)
            clon, clat = aff.to_lonlat(cx, cy)
            layers[key].append({"ring": ll, "centroid": [round(clon, 6), round(clat, 6)],
                                "px": [round(cx), round(cy)], "area_px": len(comp)})
    for key in layers:
        layers[key].sort(key=lambda c: c["px"][0])

    def poly_feats(items, prefix, extra):
        return [feature({"type": "Polygon", "coordinates": [c["ring"]]}, {
            "id": f"{prefix}-{i:02d}", "centroid_lon": c["centroid"][0],
            "centroid_lat": c["centroid"][1], "pixel_centroid": c["px"],
            "area_px": c["area_px"],
            "geometry_quality": "convex hull of traced pixels, approximate",
            **extra}) for i, c in enumerate(items, 1)]

    write_fc(args.out / "cyan_neighborhoods.geojson",
             poly_feats(layers["cyan"], "CAM-NB", {"layer": "Identified Single Access Neighborhood"}),
             "Approximate convex hulls of the cyan areas in Fig. 11-14. Adjacent neighborhoods "
             "that touch on the map merge into one blob.")
    write_fc(args.out / "calfire_subdivisions.geojson",
             poly_feats(layers["purple"], "CAM-CF-A", {"layer": "CAL FIRE subdivision, approved"})
             + poly_feats(layers["blue"], "CAM-CF-R", {"layer": "CAL FIRE subdivision, in review"}),
             "Approximate convex hulls of purple (approved) and blue (in review) outlines in "
             "Fig. 11-14. Long blue lines may be partial outlines cut by the map edge.")
    print({k: len(v) for k, v in layers.items()})

    # Overlay to check the fit by eye
    over = img.copy()
    d = ImageDraw.Draw(over)
    for r in roads:
        if r["highway"] in {"motorway", "trunk", "primary", "secondary", "tertiary"}:
            pts = [tuple(p) for p in aff.to_px(r["merc"])]
            d.line(pts, fill=(255, 0, 255), width=2)
    for s, f in zip(stars, feats):
        d.ellipse([s["px"] - 8, s["py"] - 8, s["px"] + 8, s["py"] + 8], outline=(0, 90, 255), width=2)
        d.text((s["px"] + 8, s["py"] - 16), f["properties"]["id"][-2:], fill=(0, 60, 200))
    for key, col in (("cyan", (0, 140, 120)), ("purple", (120, 0, 160)), ("blue", (0, 60, 200))):
        for c in layers[key]:
            d.polygon([tuple(p) for p in aff.to_px(np.array([to_merc(*ll) for ll in c["ring"]]))],
                      outline=col)
    over.save(args.out / "overlay_check.jpg", quality=80)


if __name__ == "__main__":
    main()
