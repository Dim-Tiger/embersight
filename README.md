# EmberSight

EmberSight is a wildfire decision-support dashboard for incident commanders and
county emergency managers in the first hours of a fire. It pulls weather, fuel,
terrain, roads, buildings and evacuation zones for one fire onto one map, and a
team of AI agents drafts a briefing, a resource request and an Incident Action
Plan (IAP). EmberSight never dispatches anything: it proposes, and a human
approves, edits or rejects every action before it counts.

> Screenshot: _(placeholder, add `docs/img/dashboard.png` here before submitting)_

Built for the 2026 Congressional App Challenge (CA-26) by Shaurya, William and
Shuntao, Viewpoint School. AI usage disclosure: _(team to add `docs/AI_DISCLOSURE.md`
before submitting, covering who and what wrote which parts, including AI tools)_.

## Run it in about 10 minutes

You need **no API keys**. Every agent has a deterministic fallback, so without
an Anthropic key the agents still run end to end and write their reports from
templates. A key only switches the narrative text and the chat to Claude.

### Prerequisites

- Node 20 or newer, and `pnpm` (`npm install -g pnpm`)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), which
  downloads the right Python for you
- Internet access (the agents read live public data feeds)

We tested these steps on macOS with Node 24, pnpm 12 and uv 0.13.

### 1. Start the agent service (terminal 1)

```bash
cd agent
uv sync --python 3.12
uv run uvicorn embersight_agent.main:app --port 8000
```

Check it from another terminal: `curl localhost:8000/healthz` prints
`{"ok":true,...}`.

### 2. Start the dashboard (terminal 2)

```bash
cd web
pnpm install
pnpm dev
```

Open **http://localhost:3000/dashboard**. (The page at `/` is a separate
marketing landing page made by someone outside the team; it is excluded from
the entry.)

If port 8000 is taken, start the agent on another port and tell the dashboard
where it is: `AGENT_BASE_URL=http://localhost:8005 pnpm dev -p 3105`.

### 3. Brief a fire

- **Real fires:** pick one from **Active Incident** in the left sidebar. The
  list comes from CAL FIRE and the national WFIGS feed, so it can be short or
  empty when California has no active fires.
- **Test fire (works any day):** click **Test Utility** (bottom right), then a
  quick-spawn button such as **Los Padres** or **Malibu**, or **Click map to
  place fire** and click the map. This switches the dashboard to Test Data and
  selects the new fire. You can also set the wind and a weather alert there.
  The synthetic wind and alert are sent to the agents; every other agent still
  reads real data for that location.

Picking a fire starts the briefing. The Operations tab shows each agent's
progress, and after about 30 to 90 seconds the **Approval Queue** (bottom
right) shows the draft IAP waiting for your decision. The Briefing, Weather,
Resources, Threats, Evacuation and IAP tabs hold each agent's findings.

### Optional extras

- `cp .env.example .env` (agent) and `cp .env.example web/.env.local`
  (dashboard), then fill in any keys you have: `ANTHROPIC_API_KEY` (Claude
  narratives and chat), `FIRMS_MAP_KEY` (NASA satellite hotspots on the map),
  `SYNOPTIC_TOKEN` (RAWS weather stations).
- `uv sync --python 3.12 --group science` adds HRRR/RTMA weather models,
  RAWS and OSMnx road routing. It needs Python 3.11 or 3.12. Without it,
  weather falls back to Open-Meteo and the NWS forecast, and each agent says
  which source it used.

## Run the tests

```bash
cd agent
uv run pytest -q
```

About 144 tests, about 5 seconds. The suite blocks all network access, so it
needs no keys and no live feeds. It covers the fire-spread math, the
values-at-risk counts, routing and rally points, evacuation zone parsing, the
agent graph's order, and the approval logic for every agent that pauses. The
dashboard has no automated tests; `cd web && pnpm typecheck` checks the
TypeScript.

## How it works

```
embersight/
  web/     Next.js 15 + React 19 dashboard (MapLibre, deck.gl, Tremor)
  agent/   Python FastAPI service that runs the agents with LangGraph
```

The dashboard draws the map and its layers (fire perimeters, satellite
hotspots, wind streams, infrastructure) straight from public feeds through
small server routes in `web/app/api/`. When you pick a fire it opens a
streaming connection to the agent service, which runs a LangGraph graph and
reports each agent's result as it finishes.

### The agent team, in the order it runs

Defined in [`agent/src/embersight_agent/graph.py`](agent/src/embersight_agent/graph.py).

1. **Orchestrator** starts the run.
2. **Weather & Wind** and **Terrain & Fuel** run in parallel. Weather tries
   RTMA, HRRR and RAWS (with the optional `science` install), then
   Open-Meteo, then the NWS forecast, and labels anything it had to assume. Terrain reads the LANDFIRE fuel model, slope and canopy.
3. When both finish, **Routing & Staging** (egress routes, staging areas,
   rally points from OpenStreetMap) and **Spread Simulation** run in parallel.
   Spread uses a simplified Rothermel rate-of-spread model and an elliptical
   fire shape, repeated 200 times with varied wind and moisture, to draw a 24
   hour probability cone.
4. **Values at Risk** counts buildings, people, hospitals, schools and power
   lines inside that cone.
5. **Resource Recommendation** and **Evacuation Intelligence** run in
   parallel. One drafts a resource request; the other compares the cone with
   published evacuation zones and may propose a zone status change.
6. **Master IC** waits for all of the above, then writes one IAP draft
   (ICS-201 for the first operational period), lists where agents disagree,
   and scores its confidence.
7. **IAP approval** pauses the run until a human decides.

After the briefing, the chat box talks to the Master IC, which can call any
specialist again (for example, re-run spread with new wind).

### How a human stays in charge

LangGraph's `interrupt()` pauses the graph and saves its state to a SQLite
checkpoint (`/tmp/embersight.db` by default). The dashboard shows the pending
item in the Approval Queue with its evidence, confidence and sources, and you
choose **Approve**, **Edit** or **Reject**. The dashboard sends that to
`/agent/resume`, and the graph continues from exactly where it paused.

What pauses during a briefing:

- **The IAP**, always, in its own `iap_approval` node. Synthesis and approval
  are separate nodes so that resuming re-runs only the approval step: the IAP
  you approve is the exact draft you read.
- **The resource request** is carried inside the IAP approval as
  `resource_request_draft`. In a briefing the resource agent marks it
  `draft_pending_iap_approval` and does not pause on its own; you sign it off
  with the IAP. (If you ask for resources in chat, that request pauses on its
  own approval card.)
- **Evacuation zone changes**: one approval per proposed change.
- **Trigger points**: Spread pauses if the cone crosses a trigger point that
  the incident defines.

Every agent that acts on your answer reads it through one helper, `hitl.decision_kind()` in
[`agent/src/embersight_agent/hitl.py`](agent/src/embersight_agent/hitl.py). It
accepts `approved`/`approve`, `edited`/`edit` and `rejected`/`reject` in any
case. Anything else counts as "unrecognized" and is treated as a refusal, so a
typo or a missing answer never approves anything. An Edit without the edited
content also leaves the IAP as a draft. Each pause and its decision is added
to the run's audit log.

Other guarantees, each covered by a test:

- No agent has a tool named `dispatch_*`, `order_*`, `send_*` or
  `publish_*`.
- The IAP uses RECOMMEND, PROPOSED and DRAFT wording. If an AI-written draft
  contains a forbidden command verb, Master IC replaces it with the
  template-written draft.
- Missing data is reported as unavailable, and estimates are labelled as
  estimates (for example, population counted from homes × 2.5 versus a rough
  area-density estimate).

## Data sources

All public. Only FIRMS and Synoptic need a (free) key, and both are optional.

| What | Source |
| --- | --- |
| Active fires | [CAL FIRE incidents](https://www.fire.ca.gov/incidents), [NIFC WFIGS incident locations and perimeters](https://data-nifc.opendata.arcgis.com/) |
| Satellite hotspots | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (needs `FIRMS_MAP_KEY`) |
| Weather and alerts | [National Weather Service API](https://www.weather.gov/documentation/services-web-api), [Open-Meteo](https://open-meteo.com/); optional NOAA HRRR/RTMA via [Herbie](https://herbie.readthedocs.io/) and RAWS via [Synoptic](https://developers.synopticdata.com/) |
| Fuel, slope, canopy | [LANDFIRE](https://landfire.gov/) (LF 2022 fuel and canopy, LF 2020 topography) |
| Map elevation | [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) |
| Roads, rally points | [OpenStreetMap](https://www.openstreetmap.org/copyright) via the [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) |
| Buildings | [FEMA USA Structures](https://gis-fema.hub.arcgis.com/) (built on [Microsoft Building Footprints](https://github.com/microsoft/USBuildingFootprints)) |
| Hospitals, schools, EOCs, power lines, cell towers | [HIFLD open data](https://hifld-geoplatform.opendata.arcgis.com/), [USGS National Map structures](https://www.usgs.gov/programs/national-geospatial-program/national-map), FEMA state EOCs |
| Evacuation zones | Cal OES active evacuations, the CAL FIRE statewide zones layer, and county Genasys (Zonehaven) zones, all through ArcGIS |
| Basemaps | [CARTO](https://carto.com/attributions) Dark Matter, [Esri World Imagery](https://www.esri.com/) |

## Known limits

- **It is a prototype.** Nobody in emergency management has used it on a real
  incident, and its numbers have not been checked against real fire outcomes.
- **Spread is simplified.** It uses a Rothermel-style formula and an ellipse,
  not a full fire simulator such as FARSITE. The `pyretechnics` package is
  listed as an optional dependency but is not called yet.
- **Resource sizing is a rough heuristic.** It sizes to the uncontained acres
  and is labelled DRAFT, but the formulas need a check by someone with fire
  operations experience. A large open fire can get very large requests.
- **Public feeds go down.** OpenStreetMap's Overpass servers are often busy;
  routing then gives up after about 45 seconds and says so. Other agents
  lower their confidence when a source fails.
- **Terrain looks at a fixed 100 × 100 km box** whatever the fire's size,
  which can make the fuel picture vague and the confidence low.
- **Master IC confidence is a harmonic mean**, so one agent at 0 makes the
  IAP show 0%. That is deliberate (a missing input should drag it down) but
  looks odd.
- **The egress-blocked rule in Evacuation Intelligence never fires** today,
  because routing does not yet publish the road graph it needs.
- **Two approvals from one chat message** cannot both be resumed yet.
- California only. Windows has not been tested.

## Built with

TypeScript, Python, Next.js, React, MapLibre GL, deck.gl, Tremor, TanStack
Query, Zustand, FastAPI, LangGraph, LangChain, Shapely, NumPy, pytest, and the
Claude API (optional). AI coding tools used to build it are listed in the
team's AI usage disclosure.
