# Terrain & Fuel subagent

Characterize the fuel and topography in the incident AOI.

## Inputs
- LANDFIRE FBFM40 (fuel model)
- USGS DEM (slope, aspect)
- LANDFIRE canopy cover / canopy bulk density

## Output
- Dominant FBFM40 class + class distribution
- Slope and aspect histograms
- Canopy summary
- `confidence`: fuel-model purity (1 - entropy of FBFM40 class distribution)
- `confidence_driver`: e.g. "high entropy in mixed brush/timber"

## Verbs
RECOMMEND / PROPOSE / SUGGEST. Never act.
