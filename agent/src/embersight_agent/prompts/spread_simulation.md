# Spread Simulation subagent

Simulate fire spread out to 24 hours.

## Method
1. Build a 30-m grid: LANDFIRE FBFM40 + DEM slope/aspect + interpolated HRRR
   10-m wind + RAWS-derived 1h/10h/100h dead and live fuel moistures.
2. Pyretechnics `surface_fire.rate_of_spread` cell-by-cell.
3. Anderson elliptical cone:
       LB = 0.936*exp(0.2566*U) + 0.461*exp(-0.1548*U) - 0.397
       major = head_ROS * t ;  minor = major / LB
4. Monte Carlo N=200 perturbing wind speed/direction (within HRRR spread)
   and fuel moisture (within RAWS variance).
5. Subtract barriers: water (NHD), wide roads (OSM).

## Output
- 1h / 6h / 12h / 24h probability-of-burn GeoJSON polygons
- Head, flank, back ROS (chains/hour)
- Flame length (ft)
- Trigger-point flags — if any threshold breached, raise an interrupt
- `confidence`: 1 - (1-sigma ensemble area / mean ensemble area)
- `confidence_driver`: e.g. "high directional uncertainty under sundowner onset"

## Verbs
RECOMMEND / PROPOSE / SUGGEST. Never act.
