# Camarillo SB 99 reference points

This folder turns Camarillo's own list of single-access neighborhoods into map data, so we can check EmberSight's no-fire "one way out" areas against an official source.

## Source

City of Camarillo, *Safety Element*, September 2024 (Rincon Consultants; Board of Forestry review packet RPC 2 (b) (i)).

- PDF: https://cdnverify.bof.fire.ca.gov/media/230nm1bt/rpc-2-b-i-camarillo-safety-element-final_adamfk.pdf (the `bof.fire.ca.gov` link returns "Access Denied" to scripts; the `cdnverify` host serves the same file)
- Page 11-42 (PDF page 46): "The Evacuation Route Analysis revealed 37 single access neighborhoods with one entry/exit and seven CAL FIRE subdivisions within the larger Camarillo area, which contain 30 or more dwelling units, located in the State Responsibility Area (SRA) or Local Responsibility Area (LRA) VHFHSZ."
- Page 11-43 (PDF page 47): Figure 11-14, "Identified CAL FIRE Subdivisions". It is a single picture with no labels in the PDF text, so every marker had to be read off the image.

## What is in here

| File | What it holds |
|---|---|
| `single_access_points.geojson` | 32 black "Single-Access Point" stars from Figure 11-14, as points (`CAM-SA-01` to `CAM-SA-32`, numbered west to east) |
| `cyan_neighborhoods.geojson` | 22 traced cyan "Identified Single Access Neighborhood" areas, as rough polygons with centroids |
| `calfire_subdivisions.geojson` | 18 purple ("Approved by CAL FIRE") and 7 blue ("In Review by CAL FIRE") outlines, as rough polygons with centroids |
| `overlay_check.jpg` | The original figure with OSM major roads (magenta), our star ids (blue) and traced outlines drawn on top. Look at this first: if the magenta roads sit on the printed roads, the coordinates are right |
| `build_reference.py` | The script that made all of the above |

Each star carries `pixel_x` / `pixel_y` (where it sits in the figure), `match_score` (how closely it matched the legend star, 0 to 1), `confidence`, and the nearest named OSM street with its distance in metres.

## Counts, and why they do not match the text

- **Stars: 32, against 37 in the text.** 29 were found automatically. 3 more sit in a knot of overlapping stars at the east end of Santa Rosa Rd (Leisure Village) and were placed by eye on a 12x zoom (`found_by: manual`). We checked the whole figure and found no others. The other 5 may be stars printed exactly on top of each other, neighborhoods outside the map frame, or neighborhoods that have no star. We cannot tell from the picture.
- **Cyan areas: 22.** Neighborhoods that touch on the map merge into one blob, so this undercounts. Use the stars, not the cyan areas, as the main list.
- **CAL FIRE outlines: 25 pieces, against 7 in the text.** The figure draws every CAL FIRE-identified subdivision in the frame, including ones south of the city near Camarillo Springs and Santa Rosa Valley. Broken outlines also come out as several pieces. The figure does not mark which 7 meet the "30 or more dwelling units in a VHFHSZ" test, so these are context only.

## How the coordinates were made

1. The map raster (1752 x 1261 px) was pulled out of PDF page 47.
2. Stars were found by template matching against the legend's own star symbol (normalised correlation of at least 0.50, legend box excluded).
3. Three intersections were read by hand from gridded close-ups and matched to their OpenStreetMap nodes: Bradley Rd / E Los Angeles Ave, La Vista Ave / W Los Angeles Ave, and Arneill Rd / E Ponderosa Dr. These give a first affine fit in Web Mercator.
4. The fit was then refined against the whole road network: thousands of OSM road vertices were projected onto the image, and the six affine numbers were adjusted to put them on the printed road lines (chamfer matching on a distance transform). Mean distance from an OSM vertex to the nearest printed line went from 2.19 px to 2.07 px.

**Accuracy.** The scale is 9.6 m per pixel. The three hand-read control points sit about 25 m (2.6 px) from the refined fit, which is roughly how precisely a road centreline can be read on this image. In `overlay_check.jpg` the OSM roads land on the printed roads across the whole frame, including the far corners (Central Ave, Somis Rd, Reino Rd). Treat each point as good to about 30 to 50 m: 25 m of fit error, plus the star symbol itself, which is about 120 m across on the ground.

## How to compare EmberSight against this list

Run EmberSight's **no-fire SB 99 view** over Camarillo with the same rules for every run, and export its single-exit areas: each area's polygon, its exit (cut) intersection, and its home count.

Then, for each Camarillo star:

1. **Hit:** an EmberSight single-exit area contains the star, or its exit intersection is within **150 m** of the star. The star marks the access point, and EmberSight's cut node can sit one block away, where the neighborhood road meets the collector, so 150 m allows for that plus the 50 m position error.
2. **Near miss:** the closest EmberSight area or exit is 150 to 300 m away. Look at these by hand on the map before calling them hits or misses.
3. **Miss:** nothing within 300 m.

Then, for each EmberSight area inside the city boundary that matched no star, count it as an **extra**.

Report three numbers: hits / 32, misses / 32, and extras. Also report recall (hits / 32) with and without EmberSight's 30-home threshold, because the city's 37 neighborhoods have no size threshold and many Leisure Village clusters are small.

### What a miss usually means

- **OSM shows a second way out that the city did not count.** Typical causes: an emergency-access road, a gated back road, a private road or driveway, or a parking-lot connection. Check the OSM tags on the second route (`access=private` or `no`, `barrier=gate`, `service=emergency_access`). If routes with those tags should not count, the fix is a rule in our graph, not a change to the city's list.
- **Size threshold.** The area is real, but EmberSight hid it because it has fewer than 30 homes.
- **Too small for OSM.** OSM does not map the private lanes inside the neighborhood, so EmberSight never sees it as its own area.
- **Position or labelling error on our side.** Check `confidence` and `found_by`. The three manual stars, `CAM-SA-28` to `CAM-SA-30`, are the least certain.

### What an extra usually means

- **A road missing from OSM.** A real second road is not mapped, so EmberSight thinks there is one way out when there are two. Check aerial imagery, then fix it in OSM.
- **A genuine gap in the city's list.** The city's analysis focused on very high fire hazard zones and on the east side. That is worth saying in the video only after a person has checked it on imagery.
- **Outside the city's scope:** for example, outside the city boundary or outside a VHFHSZ.

## Known limits

- These points come from a picture, not from the city's GIS data. The best fix is to ask the City of Camarillo (Community Development) or Rincon Consultants for the SB 99 single-access layer. It is public planning data, and it would replace this folder outright.
- The figure itself is dated 2024 and uses CAL FIRE's 2007 and 2024 hazard zones. Roads built since then are not reflected.
- The polygons are convex hulls of traced pixels. They are fine for "is this area here" and too rough for area or home counts.

## Reproducing

```bash
python3 -m venv .venv && .venv/bin/pip install pymupdf numpy scipy pillow
curl -L -o safety.pdf "https://cdnverify.bof.fire.ca.gov/media/230nm1bt/rpc-2-b-i-camarillo-safety-element-final_adamfk.pdf"
curl -A "EmberSight-validation/0.1 (student project)" -o roads.json \
  --data-urlencode 'data=[out:json][timeout:120];way["highway"~"^(motorway|motorway_link|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)$"](34.16,-119.13,34.28,-118.94);out tags geom;' \
  https://overpass-api.de/api/interpreter
.venv/bin/python build_reference.py --pdf safety.pdf --roads roads.json
```

The OSM roads used here were fetched on 2026-10-22. A later fetch can change nearest-street names and move the refined fit by a metre or two; re-check `overlay_check.jpg` after any re-run.

*Built with Claude (AI) for the EmberSight team. See the AI disclosure in the PR.*
