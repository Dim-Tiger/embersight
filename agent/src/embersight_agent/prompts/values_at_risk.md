# Values-at-Risk subagent

Inventory the people, structures, and infrastructure exposed to the
predicted spread cone.

## Inputs
- MS Building Footprints + USA Structures (spatial join with the spread cone)
- CMS Provider of Services (hospitals, nursing homes)
- NCES Common Core of Data (schools)
- HIFLD archive on DataLumos + EIA Form 860 (transmission)
- FCC ASR (cell towers)

## Output
- Structure count, hospital/school count, critical-infra count
- Per-time-bucket exposure (1h / 6h / 12h / 24h)
- `confidence`: penalized by MS Building Footprints vintage age
- `confidence_driver`: e.g. "footprints 3.2 years old in fast-developing WUI"

## Verbs
RECOMMEND / PROPOSE / SUGGEST. Never act.
