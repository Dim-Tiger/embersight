# Routing & Staging subagent

Identify ingress/egress routes and score candidate staging areas for an
incident, with explicit wind awareness and a transparent multi-axis score.

## Inputs

- **OSMnx-derived street graph** for the 25 km AOI (`drive` network).
- **OSM Overpass** features: water (lakes/rivers/streams), fire stations,
  paved areas (industrial / commercial / parking / paved highways).
- **Upstream weather** (`weather_wind.payload.wind_dir_deg`,
  `wind_speed_mph`, optional `critical_window`) — MET FROM convention.
- **Upstream terrain** (`terrain_fuel.payload.terrain.elevation_m.mean`
  and `terrain_fuel.payload.slope_pct`) — used as real DEM proxy for
  comms LOS and apparatus-parking suitability.

## Scoring axes (weights sum to 1.0)

| Axis        | Weight | Logic                                                              |
|-------------|--------|--------------------------------------------------------------------|
| incident    | 0.28   | Smooth tent: 0 inside 2 km standoff, peak at 5 km, 0 by 15 km.     |
| water       | 0.22   | Nearest water feature; linear decay 0–8 km.                        |
| station     | 0.16   | Nearest fire station; linear decay 0–12 km.                        |
| paved       | 0.10   | Nearest paved surface; linear decay 0–2 km.                        |
| elevation   | 0.08   | AOI mean elevation / 1500 m. Comms LOS proxy.                      |
| slope       | 0.08   | Gentle (≤8%) → 1.0; unusable (≥25%) → 0.0.                         |
| wind        | 0.08   | Upwind of fire → 1.0; crosswind → 0.55; downwind → 0.0.            |

Candidates within 400 m of an already-accepted candidate are dropped so
a single industrial complex doesn't yield five "distinct" recommendations.

## Egress routing

Eight compass bearings (N, NE, E, SE, S, SW, W, NW). For each, the
agent finds the best-aligned major-road node 5–25 km from the incident
(with a 10 km sweet spot) and computes a drivable shortest path. Bearings
are then reordered so **upwind / crosswind routes — away from the fire-head
heading — surface first**. Each route is tagged `upwind / crosswind /
downwind` for the UI legend.

## Output

- `primary_routes`: top-3 ingress routes from the top staging candidate
  (LineString + length_km + est_drive_minutes + avg_speed_kph + bearing).
- `egress_routes`: up to 5 outward routes, wind-ranked, with bearing
  label and wind relation.
- `candidates`: top-5 with `score`, `score_components`, `score_weights`,
  `score_raw` for full audit transparency.
- `confidence`: derived from road-graph availability, OSM density,
  presence of upstream weather/terrain, and Overpass failures.
- `confidence_driver`: human-readable explanation chain.

## Verbs

RECOMMEND / PROPOSE / SUGGEST. Never act — never use `dispatch`,
`order`, `send`, `publish`, `assign`, or `deploy`.
