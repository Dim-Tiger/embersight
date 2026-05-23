EmberSight: Technical Research Report for a 24-Hour Hackathon MVP
TL;DR
Build EmberSight as a Next.js + TypeScript app with a Python FastAPI sidecar; use LangGraph (Python) for the hierarchical orchestrator + 7 subagents, bridge to the frontend via the Vercel AI SDK Data Stream Protocol, and render the dashboard with MapLibre GL JS + deck.gl + shadcn/ui + Tremor. This pairing gives you the strongest hierarchical orchestration, the cleanest streaming UX, and the highest-fidelity geospatial visuals within 24 hours.
For credibility, wire up four real, no-auth public endpoints in the first 4 hours: CAL FIRE IncidentApi/List, NIFC WFIGS Current Incident Locations + Perimeters, NASA FIRMS VIIRS area API, and NWS api.weather.gov forecasts/alerts. Layer in LANDFIRE LFPS fuel rasters, Cal OES CA_EVACUATIONS, and a Python Rothermel implementation (mitrefireline/simfire or PyFireCoimbra/PyFireStation) for the Spread Simulation agent — this is what wins demo-credibility against paid tools like Technosylva despite being free.
The agentic team must put humans in the loop at three explicit choke points: (1) the Master IC's IAP draft requires the human IC's signature before the operational period can commit, (2) the Resource Recommendation agent's dispatch list requires acknowledgment, and (3) reactive emergent suggestions (wind shift, RH drop) appear as toast notifications with "accept / dismiss / ask for more." This maps cleanly onto the ICS Planning P and is the entire judging hook.
Key Findings
1. Agentic Framework — Pick LangGraph (Python), call it from Next.js via the AI SDK
After comparing the seven candidates against EmberSight's specific requirements (hierarchical orchestrator → 7 parallel domain subagents → synthesis agent, streaming intermediate reasoning to a Next.js UI, MCP/tool support, 24-hour ergonomics), LangGraph is the right primary choice, with the Claude Agent SDK as a strong runner-up if you want subagents to "just work" out of the box.
Framework
Hierarchical orch
Parallel subagents
TS/Python
Streaming
24-hr ergonomics
Verdict
LangGraph
Native (graph + Send API for fan-out)
Yes, first-class
Both (Python is mature)
Per-node streaming via astream_events
Steeper than CrewAI (40-60 LOC vs 15 for a basic agent) but worth it
Primary pick
Claude Agent SDK
Native agents:{} parameter, parent invokes via the Task tool
Yes, designed for it
Both
Async iterator of typed messages
Excellent — literally agents: { 'weather-wind': {description, prompt, tools, model}, ... }
Strong backup if Claude-only
CrewAI
Hierarchical process w/ manager
Limited parallelism
Python only
Limited
Fastest (~20 LOC), but limited control flow
Not ideal — fire-domain requires conditional branching
OpenAI Agents SDK
Handoffs, not hierarchy
Possible but ad-hoc
Python + TS
Yes
Easy if all-OpenAI
OpenAI lock-in is a hackathon liability
Microsoft AutoGen / AG2
GroupChat is peer, not hierarchical
Yes (conversational)
Python primary
Yes
20+ LLM calls per task is too expensive for 7 subagents
Skip
PydanticAI
Manual orchestration
DIY
Python only
Yes
Type-safe but verbose for multi-agent
Skip for MVP
Mastra
Supervisor + subagents pattern
Yes
TypeScript-native
Vercel AI SDK-native
Cleanest TS DX, single-language stack
Use this if you refuse to add Python

Why LangGraph wins for EmberSight:
The fire-domain reasoning is inherently a graph: Weather/Wind and Terrain/Fuel must complete before Spread Simulation; Values-at-Risk and Routing/Staging can run in parallel after Spread; Evacuation Intelligence depends on Values-at-Risk; Master IC synthesizes all. LangGraph's StateGraph plus Send for parallel fan-out is purpose-built for this.
LangGraph 1.0 was released October 22, 2025 as the first stable major release in the durable-agent framework space, per LangChain's official announcement — quoting LangChain: "After more than a year of powering agents at companies like Uber, LinkedIn, and Klarna, LangGraph is officially v1." It has first-class checkpointing (Postgres or SQLite) — useful if you want the "what changed in this operational period?" replay.
Streaming is first-class via astream_events; each node emits typed events that map directly to UI cards.
The Python ecosystem matters because the Spread Simulation, fuel-raster handling (rasterio), and any Rothermel/simfire code are Python-native. A pure-TS framework forces you to shell out anyway.
Backend choice that follows: a FastAPI sidecar exposing one /api/agent/stream SSE endpoint that runs the LangGraph. Next.js calls it via the Vercel AI SDK's custom transport — there is an official Vercel template ai-sdk-preview-python-streaming showing exactly this Data Stream Protocol bridge. All UI, auth, and routing stay in Next.js.
If you want to gamble on TypeScript-only: Mastra is the only credible TS-native alternative. It is built on the Vercel AI SDK, has supervisor + subagents primitives, Zod-typed tools, and a Studio for trace replay. It would shave the FastAPI sidecar but limits you to JS-native fire libraries (essentially just @emxsys/behave Rothermel JS port, no LANDFIRE rasterio).
If you want to gamble on Claude-only: The Claude Agent SDK's agents parameter + Task tool + allowedTools model is dead-simple for hierarchical work; subagents inherit nothing except the Task prompt, get their own context, and return only a final message. Anthropic's engineering post "Building a multi-agent research system" (June 2025) explicitly recommends this for the orchestrator-spawns-specialists pattern, noting: "Each subagent needs an objective, an output format, guidance on the tools and sources to use, and clear task boundaries. Without detailed task descriptions, agents duplicate work, leave gaps, or fail to find necessary information." That's a non-negotiable design rule for EmberSight.
2. Fire Progression Models — MVP Stack
Ship three layers of fire spread, in increasing fidelity, only as time permits:
Layer 1 (must ship, 1-2 hours): Probabilistic spread cone. Given an ignition point, wind vector, and a 3-class fuel raster (or a constant if no raster yet), compute an elliptical spread polygon using Anderson's (1983) wind-driven ellipse, parameterized by Rothermel rate of spread. Length-to-breadth ratio LB = 0.936·exp(0.2566·U) + 0.461·exp(-0.1548·U) - 0.397, where U is mid-flame wind speed (mph) — the same shape FARSITE uses. Render as a GeoJSON polygon. The literature ground truth: USDA RMRS-GTR-371 (Andrews 2018), the canonical reference for Rothermel + the Anderson ellipse.
Layer 2 (stretch, +3 hours): Python Rothermel rate-of-spread. Use mitrefireline/simfire (MIT license, pip-installable, runs the actual Rothermel surface model on Scott & Burgan fuel rasters; renders a PyGame GIF that looks great on the demo screen) or the simpler PyFireCoimbra/PyFireStation (single-file FuelModel class — Lopes et al. 2002). For the absolute fastest path, copy the public-domain Rothermel implementation at prairieprojectknowledgehub.org — it is ~60 lines and validated against BehavePlus.
Layer 3 (stretch, only if pipelines are running smoothly): ELMFIRE. Open-source (lautenberger/elmfire on GitHub), but a Fortran binary with significant fuel-prep overhead. The Salo.ai docs are honest: "It is difficult to work with, however, and we've written a number of scripts and wrappers." Skip for the demo; reference it as "production roadmap."
Hosted services:
Pyregence/PyreCast publishes a public viewer (pyrecast.org) but the EIS HTTP API at https://api.pyrecast.org/eis-api/v0/ requires a Bearer auth token issued by Pyregence — not feasible to obtain inside a hackathon weekend. Use the published map viewer only as inspiration.
WIFIRE Firemap is gated to fire agencies/researchers; the WIFIRE Commons catalog at https://wifire-data.sdsc.edu is a CKAN registry (use /api/3/action/package_search?q=fire for discovery), but most "datasets" are pointers to ArcGIS services that you can query directly. There is no unified WIFIRE REST API.
Recommendation: Implement Layer 1 in the Spread Simulation subagent's tool. If you have time, swap in simfire for visual oomph.
3. CAL FIRE & California Data — The Exact Endpoints (all no-auth unless noted)
Source
Endpoint
Format
Notes
CAL FIRE active incidents
https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false
JSON array
Fields: Name, Latitude, Longitude, AcresBurned, PercentContained, County, IsActive, Started, Updated, UniqueId. Use ?year=2025&inactive=true for historical.
CAL FIRE GeoJSON variant
https://www.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=true
GeoJSON
Known regression: only current-year. Prefer List and build GeoJSON yourself.
NIFC WFIGS incident points (current)
https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState%3D%27US-CA%27&outFields=*&f=geojson
GeoJSON
Refreshed every 5 min.
NIFC WFIGS perimeters (current)
https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query?where=1=1&outFields=*&f=geojson
GeoJSON
Fall-off rules retire stale records.
Cal OES evacuation zones (Zonehaven aggregate)
https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query?where=1=1&outFields=*&f=geojson
GeoJSON
Status field: NORMAL, EVACUATION WARNING, EVACUATION ORDER. Zonehaven schema.
CAL FIRE FRAP Fire Hazard Severity Zones
ArcGIS feature service via https://hub-calfire-forestry.hub.arcgis.com/ (search "FHSZ")
GeoJSON / Esri JSON
SRA + LRA polygons. Use for "values at risk + hazard class."
NASA FIRMS active fire (VIIRS)
https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/-124,32,-114,42/1
CSV
Free MAP_KEY. Quoted from FIRMS docs: "MAP_KEY limit is 5000 transactions / 10-minute interval. Larger transactions may count as multiple requests (ex. requesting 7 days)." Ultra-Real-Time available for US/Canada.
NWS forecast (point)
Step 1: GET https://api.weather.gov/points/{lat},{lon} → grab properties.forecastHourly. Step 2: GET that URL.
JSON
Requires User-Agent header. ~1 sec between calls advised.
NWS alerts (Red Flag, Fire Wx Watch)
https://api.weather.gov/alerts/active?point={lat},{lon}
JSON-LD / CAP
Red Flag Warning is coded Fire Weather Warning in the API event taxonomy.
Synoptic Mesonet (RAWS)
https://api.synopticdata.com/v2/stations/latest?radius={lat},{lon},20&network=2&vars=air_temp,relative_humidity,wind_speed,wind_direction&token={TOKEN}
JSON
Free token after signup; network=2 is RAWS.
LANDFIRE fuel rasters (FBFM40, slope, aspect, canopy)
GET https://lfps.usgs.gov/arcgis/rest/services/LandfireProductService/GPServer/LandfireProductService/submitJob?Layer_List=240FBFM40&Area_Of_Interest={W}%20{S}%20{E}%20{N}&f=json, then poll /jobs/{jobId} and fetch /jobs/{jobId}/results/Output_File
Zipped multiband GeoTIFF
Async Esri GP service. Use the landfire-python package on PyPI.
OSM Overpass (roads, hydrants, hospitals near fire)
https://overpass-api.de/api/interpreter with QL query
JSON / OSM
Free, rate-limited. Use for hydrants and evac routes.
PurpleAir / AirNow
AirNow API at https://www.airnowapi.org/aq/observation/zipCode/current/ (free key); PurpleAir at https://api.purpleair.com/v1/sensors (signup)
JSON
PM2.5 for smoke.
HIFLD (schools, hospitals, power, water)
Browse https://hifld-geoplatform.opendata.arcgis.com/; each layer has an ArcGIS REST /query endpoint
GeoJSON
Free, no auth.
Microsoft Building Footprints (USA)
https://github.com/microsoft/USBuildingFootprints (per-state GeoJSON)
GeoJSON files
Static dataset; download California once, serve from your backend.

IRWIN is the master interagency reporting database that feeds WFIGS — there is no public IRWIN API; access is via WFIGS layers. Don't try to query IRWIN directly.
RAWS: official access is via FAMWeb/WIMS, but those require federal credentials. The Synoptic Data Mesonet API is the practical public proxy (their open-access tier is free; the National Mesonet Program contract makes RAWS data flow through it).
Spot Forecast: NWS Spot Forecasts are requested manually by Incident Commanders via a separate web form (no public API). For the MVP, the standard api.weather.gov/gridpoints/{wfo}/{x},{y} hourly forecast is the closest publicly available proxy and is what your Weather & Wind subagent should call.
4. CAL FIRE / NWCG IMT Roles & ICS Mapping
CAL FIRE uses the National Wildfire Coordinating Group (NWCG) position taxonomy under the California Interagency Incident Command System (CICCS). Per the 2022 CICCS Qualification Guide and NWCG position pages:
Incident Types (PMS 200 / NIMS):
Type 1: Largest, most complex incidents. Full ICS structure, all command and general staff filled, multiple branches/divisions. National-level IMTs.
Type 2: Large incidents, multiple operational periods, may have multiple agencies. State / interagency CIMTs.
Type 3: Extended initial attack to multi-day incidents. Local IMTs.
Command & General Staff (the agents you should model first):
Incident Commander (IC) — overall responsibility, approves the IAP. Map to Master Incident Commander synthesis agent.
Operations Section Chief (OSC) — tactical execution, supervises Branches/Divisions/Groups. Map to Routing & Staging + Resource Recommendation agents.
Planning Section Chief (PSC) — drives IAP development; supervises Resources Unit, Situation Unit, Documentation Unit, Demobilization Unit. Map to Spread Simulation + Values-at-Risk agents (under SITL/FBAN).
Logistics Section Chief (LSC) — facilities, supplies, comms, medical. Optional agent for stretch.
Finance/Administration Section Chief (FSC) — cost tracking. Skip for MVP.
Safety Officer (SOF), Public Information Officer (PIO), Liaison Officer (LOFR) — Command Staff. Safety Officer can be a quick agent that surfaces Red Flag warnings + LCES checks.
Key Technical Specialists:
Fire Behavior Analyst (FBAN) — collects weather/fuels data, predicts fire growth for the current and next operational period. Reports to SITL or PSC. Map directly to the Weather & Wind + Terrain & Fuel + Spread Simulation agents combined.
Long-Term Fire Analyst (LTAN) — multi-day strategic projections. Map to a "long-term outlook" sub-mode of the Spread Simulation agent. Per NWCG: "responsible for collecting weather data, developing strategic and tactical fire behavior information, predicting fire growth, and interpreting fire characteristics for use by incident overhead."
Situation Unit Leader (SITL) — maintains the common operating picture. This is essentially the Master IC dashboard view.
GIS Specialist (GISS) — produces maps. Your frontend IS the GISS.
Resources Unit Leader (RESL) — tracks all assigned resources (ICS 211, ICS 219 T-Cards). Map to the Resource Recommendation agent.
Documentation Unit Leader (DOCL) — assembles the IAP. Map to the IAP drafting output of the Master IC agent.
Incident Meteorologist (IMET) — NWS-detailed meteorologist on large incidents. Subordinate to FBAN. Map to Weather & Wind.
IAP Forms (the artifacts your Master IC agent should output):
Form
Title
Who fills it
EmberSight mapping
ICS 202
Incident Objectives
PSC, IC approves
LLM-drafted from Master IC synthesis
ICS 203
Organization Assignment List
Resources Unit
Auto-populated from RESL agent
ICS 204
Assignment List (per Division/Group)
Resources + OSC
One per division; populated from Routing/Staging agent
ICS 205
Communications Plan
Comms Unit Leader
Static template
ICS 206
Medical Plan
Medical Unit Leader
Static template + nearest hospital from HIFLD
ICS 207
Org Chart
Resources Unit
Auto-rendered from agent graph
ICS 208
Safety Message/Plan
Safety Officer
LLM-drafted from Red Flag + LCES
ICS 209
Incident Status Summary
Situation Unit
Generated from all subagent outputs
ICS 211
Check-In
RESL
Static
ICS 213
General Message
Anyone
Static
ICS 214
Activity Log
All sections
Auto-logged from agent traces
ICS 215
Operational Planning Worksheet
OSC + tactics meeting
Drafted by Routing/Staging + RESL agents
ICS 215A
IAP Safety Analysis
Safety Officer
Companion to 215, LLM-drafted with risk matrix
ICS 220
Air Operations Summary
Air Ops Branch Director
Skip for MVP

The Planning P (FEMA's Incident Action Planning Process diagram, formalized in NFPA 1561 Annex C and FEMA IS-200): the lower leg of the P is initial response (notification → initial briefing → initial UC meeting → IC/UC develop objectives). The upper loop, repeated each operational period (typically 12 hours for active wildfires, 24 hours late in incident), is:
IC/UC objectives meeting (review/update objectives)
Command & General Staff meeting
Preparing for Tactics Meeting (OSC builds ICS 215)
Tactics meeting (OSC, LSC, Safety, RESL — confirm resources)
Planning meeting (full C&G Staff confirms plan)
IAP prep & approval (PSC compiles, IC signs)
Operational Period Briefing — IAP distributed to supervisors
Execute plan & assess progress → loop back to (1)
Map the EmberSight UI to this loop. The dashboard should have an explicit "Operational Period" header with a countdown, and "Generate next-period IAP draft" should be the dominant CTA at hours 10-11 of each period.
Spot Forecast process: when fuels/weather change dramatically mid-operational-period, the IC requests a Spot Forecast from the local NWS Weather Forecast Office via a web form. Forecaster issues a custom forecast for the fire's lat/lon and elevation, usually within 30 minutes. In EmberSight, your "reactive emergent suggestion" feature when wind shifts or RH drops below threshold is functionally a Spot Forecast trigger — that's the framing to use with judges.
5. Frontend Architecture
Mapping: MapLibre GL JS + deck.gl is the right call. Per MapLibre's official GitHub README: "It originated as an open-source fork of mapbox-gl-js, before their switch to a non-OSS license in December 2020. The library's initial versions (1.x) were intended to be a drop-in replacement for the Mapbox's OSS version (1.x) with additional functionality, but have evolved a lot since then." In other words, treat MapLibre as a sibling, not a literal drop-in for Mapbox v2/v3. deck.gl on top gives you GPU-accelerated polygon layers (fire perimeters, spread cones, evac zones) and a ScatterplotLayer for structures-at-risk. Use the MapboxOverlay (works with MapLibre too, despite the name) with interleaved: true so deck.gl polygons render under MapLibre's text labels — this is the polished look judges notice.
Mapbox GL JS v3 is also great (better terrain, hillshade, Mapbox Standard style), and the free tier covers a hackathon. Use it if you want 3D extrusion of buildings near the fire. Don't use Cesium — 3D globe is overkill for a county-scale incident and the asset pipeline burns hours.
UI components: shadcn/ui (Tailwind + Radix) for the chrome + Tremor for the dashboard charts/cards. Tremor's <Card>, <Metric>, <AreaChart>, <BarList>, and <Tracker> are designed for exactly this kind of operational dashboard and cost zero engineering effort. shadcn provides the Sheet, Dialog, Tabs, Command palette for the rest. Skip Material UI (heavy + Material aesthetic is wrong for this domain) and Mantine (less momentum in 2026).
Streaming: Vercel AI SDK (useChat, streamText, custom transport). The official ai-sdk-preview-python-streaming template wires Next.js → FastAPI via the Data Stream Protocol so you can stream LangGraph node events into React UI cards. For raw agent traces, expose them as data parts in the stream and render with a <MessagePart> switch. SSE is the transport under the hood; you don't have to think about it.
Data viz: Recharts (under Tremor) for line/area + Visx for anything custom (e.g., a polar wind rose, a fire-behavior triangle plot). For the FBAN-style fire behavior graphs (rate of spread vs. wind speed, flame length curve), Visx is worth the hour.
State: Zustand for client state + TanStack Query (React Query) for server state. Zustand for the selected incident, active subagent tab, map viewport. TanStack Query for all REST polling (CAL FIRE list every 60s, WFIGS perimeters every 5 min, NWS every 10 min). Don't reach for Jotai unless you already know it.
3D terrain: Mapbox 3D terrain (addSource('mapbox-dem') + setTerrain) is free with the Mapbox SDK and gives you the WOW moment in 5 minutes. Cesium is not worth the integration cost.
6. Backend & Deployment
Repo shape: Single Next.js app + a sibling agent/ Python package, deployed as two services (no Turborepo needed for 24 hrs). Use pnpm + a Makefile or justfile for orchestration. Frontend on Vercel (Hobby tier is free), Python on Railway or Fly.io (both have $5 free credit, sub-minute deploys, and built-in HTTPS). Avoid putting Python in a Vercel Function — agent runs are too long.
Long-running workflows: Stream everything. The orchestrator + 7 subagents finishing in ~30-60s is acceptable if the user sees subagent progress in real time. Use SSE via FastAPI's StreamingResponse or EventSourceResponse (sse-starlette). LangGraph's astream_events("v2") emits a structured event for every node start/end/token — pipe these to AI SDK Data Stream Protocol parts. For runs >2 min, push to a Redis queue + a worker, but you likely won't need this.
LLM choice: Claude Sonnet 4.5 for the Master IC + Spread Simulation (reasoning-heavy) and Claude Haiku 4.5 (or GPT-4o-mini) for the 6 domain subagents (mostly summarize + cite). Per Anthropic's published pricing (platform.claude.com/docs/en/about-claude/pricing), Claude Sonnet 4.5 is $3.00 input / $15.00 output per million tokens; Claude Haiku 4.5 is $1.00 input / $5.00 output per million tokens. Modeling a typical operational-period run at ~10K input + 1K output tokens per call, seven Sonnet calls cost ≈ $0.315 and seven Haiku calls cost ≈ $0.105 per period — well under hackathon credits.
Vector DB / RAG: Skip for v1. Stuff ICS form templates (ICS 202, 204, 215A, etc.) and the NWCG Incident Response Pocket Guide (IRPG) directly into system prompts of the relevant agents as static markdown. If you have time, drop in Chroma (in-process, 5 min setup) and embed the 50-ish IRPG pages. Pinecone/Qdrant are overkill.
7. Open-Source Inspiration / Forks
mitrefireline/simfire (MIT) — Rothermel-based Python simulator with PyGame visualization. Has BurnMD historical data layer. Cite this in your demo.
lautenberger/elmfire — production-grade ELMFIRE source; reference for "production roadmap."
forefireAPI/forefire — C++ wildfire engine with Python bindings, used in coupled fire-atmosphere research (CNRS / Université de Corse).
PyFireCoimbra/PyFireStation — clean Python Rothermel.
emxsys/behave — Rothermel in JavaScript (useful if you go pure-TS with Mastra).
fire2a/C2FSB and fire2a/C2FK — Cell2Fire + Scott & Burgan / KITRAL fuel systems.
pyregence/pyregence — Pyregence/PyreCast web portal source (Clojure). Worth skimming the UI design.
stiles/ca-fires-history — example scraper of the CAL FIRE IncidentApi endpoint.
vercel-labs/ai-sdk-preview-python-streaming — official Next.js + FastAPI + AI SDK template; this is your starter.
NIFC ArcGIS Hub (data-nifc.opendata.arcgis.com) for layer browsing.
WIFIRE Commons notebooks at https://wifire-data.sdsc.edu for fuel-loading examples.

Details
Deliverable 1 — Full-Stack Framework Explanation
┌─────────────────── Browser ─────────────────────┐
│ Next.js (App Router) + Tailwind + shadcn/ui     │
│ • MapLibre GL JS + deck.gl (map)                │
│ • Tremor (dashboard cards/graphs)               │
│ • Visx (fire behavior plots)                    │
│ • Vercel AI SDK useChat (agent streaming)       │
│ • Zustand (UI state) + TanStack Query (REST)    │
└──────────┬──────────────────────────┬───────────┘
       SSE │                          │ REST polls
           ▼                          ▼
┌──── Next.js API routes ────┐   ┌── External APIs ──┐
│ /api/agent/stream (proxy)  │   │ CAL FIRE Incidents│
│ /api/data/incidents (BFF)  │   │ NIFC WFIGS        │
│ /api/data/weather (BFF)    │   │ NASA FIRMS        │
│ ...thin proxies, caching   │   │ NWS api.weather   │
└──────────┬─────────────────┘   │ Cal OES Evac      │
           │ HTTP                │ Synoptic Mesonet  │
           ▼                     │ LANDFIRE LFPS     │
┌──── FastAPI (Python) ──────┐   │ Overpass/HIFLD    │
│ /agent/stream  (SSE)       │   └───────────────────┘
│ LangGraph orchestrator     │
│  ├─ Weather & Wind agent   │
│  ├─ Terrain & Fuel agent   │
│  ├─ Spread Simulation agent│←── simfire / Rothermel
│  ├─ Values-at-Risk agent   │
│  ├─ Routing & Staging agent│
│  ├─ Resource Rec agent     │
│  ├─ Evacuation Intel agent │
│  └─ Master IC synth agent  │
│ Anthropic + OpenAI clients │
└────────────────────────────┘

Why this stack:
One language barrier (TS↔Python) is cheaper than rewriting Rothermel/simfire and rasterio in JS.
LangGraph's checkpointed state means the "Operational Period" can pause and resume, mirroring how IMTs actually work.
Vercel AI SDK Data Stream Protocol is already bridged to Python via the official template — zero glue code.
Tremor + shadcn + MapLibre = "looks like Palantir Foundry" out of the box, the right aesthetic for IMT judges.
Deliverable 2 — Technical Buildout Roadmap (hour-by-hour)
Hour 0-2: Skeleton
npx create-next-app embersight --ts --tailwind --app and npx shadcn@latest init.
Add Tremor: npm i @tremor/react. Add map: npm i maplibre-gl @deck.gl/core @deck.gl/layers @deck.gl/mapbox react-map-gl.
Scaffold FastAPI: uv init agent && uv add fastapi uvicorn langgraph langchain-anthropic langchain-openai sse-starlette httpx rasterio shapely pyproj python-dotenv.
Wire /api/agent/stream Next.js route → FastAPI /agent/stream SSE.
Drop in the ai-sdk-preview-python-streaming Data Stream Protocol bridge.
Hour 2-6: Real CAL data on a real map 6. GET https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false → cache in-memory 60s in a Next.js Route Handler → render as a deck.gl ScatterplotLayer colored by PercentContained. 7. GET https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState='US-CA'&outFields=*&f=geojson for cross-validation; merge by IRWIN_IncidentName. 8. WFIGS perimeters → GeoJsonLayer with red fill, 0.4 opacity. 9. Add CalOES evac zones overlay (toggleable layer): GeoJsonLayer colored by status (yellow=WARNING, red=ORDER). 10. Click on an incident → opens a Sheet with Tremor <Metric> cards for Acres, %Contained, Started, plus a "Run analysis" button that fires the agent.
Hour 6-12: Agent team 11. Define LangGraph StateGraph with state: {incident, weather, terrain, spread_polygon, values_at_risk, routes, recommendations, evacuation_summary, iap_draft, messages[]}. 12. Implement 7 subagent nodes, each a function calling ChatAnthropic with tool_use, plus one "Master IC" node. 13. Wire parallel fan-out using the Send API from a "fan_out" node to Weather/Terrain/Values/Routing simultaneously. 14. Each subagent has 1-3 tools, defined as Python functions wrapped with LangChain @tool: - get_nws_forecast(lat, lon) → calls api.weather.gov/points then gridpoints/.../forecast/hourly - get_red_flag_alerts(lat, lon) → calls api.weather.gov/alerts/active?point=... - get_raws_observations(lat, lon, radius_mi) → Synoptic API - get_landfire_fuels(bbox) → submits LFPS job, polls, returns local cached path - compute_spread_polygon(ignition, wind, fuel_class, slope) → Rothermel + Anderson ellipse, returns GeoJSON - query_overpass(bbox, key, value) → OSM POIs (hospitals, schools, hydrants) - get_buildings_in_polygon(polygon) → spatial join against pre-downloaded Microsoft Building Footprints - get_evacuation_zones(bbox) → Cal OES feed 15. Master IC system prompt: include the 8 IAP form structures, the Planning P, and explicit instruction to always emit (a) a draft ICS 202 in JSON and (b) a "human approval required" flag. 16. Stream every node event via astream_events → write AI SDK data parts → render as a live "agent activity" feed in the right sidebar.
Hour 12-18: Polish 17. Build dedicated tabs: - Operations (default): Map + collapsible right sidebar with AgentFeed - Weather: Tremor AreaChart of next-24h wind/RH/temp from NWS gridpoints + Red Flag alert banner if active - Resources: Tremor BarList of recommended dispatches (engines, crews, dozers, air tankers, helicopters) with rationale tooltips - Threats: Sortable table of structures at risk (address, distance to fire, fuel hazard severity zone from CAL FIRE FRAP) + Tremor Metric tiles for total structures, schools, hospitals, critical infra - Evacuation: Cal OES zones list synced to map; zone codes and statuses - IAP: Rendered ICS 202 + 215 + 215A as printable cards; Approve button enables PDF export 18. Implement the "Reactive Emergent Suggestion" panel: a background worker polls NWS alerts and current RAWS observations every 5 min; if wind direction changes >30° or RH drops below 15%, emit a toast + a "Wind shift detected — re-run spread simulation?" CTA. 19. IAP draft view: pretty-printed ICS 202 + 215 with "Approve & Lock for Operational Period" button. Approval stores a checkpoint in LangGraph SQLite. 20. Add a "human-in-the-loop" rejection path: every subagent output card has thumbs-up/down + a "request clarification" box that re-runs that subagent with the human's added context (this is the demo's punchline).
Hour 18-24: Demo prep 21. Deploy Next.js to Vercel, FastAPI to Railway. Set env vars. 22. Seed with a recent real CA incident; if none active, drop a synthetic ignition near a known WUI (e.g., Paradise, Malibu). 23. Record a 90-sec demo script that walks through: ignition appears → Master IC fans out to 7 subagents (visible activity feed) → spread cone overlays on map → values-at-risk shows 1,200 structures + 2 schools + 1 hospital → routing shows 3 staging areas → human-in-the-loop refinement → IAP draft appears → Operational Period clock starts.
Deliverable 3 — IMT Roles & Agent Mapping
EmberSight Agent
ICS Position(s) Augmented
Primary IAP Forms Touched
Weather & Wind
FBAN, IMET
ICS 202 weather block, 215
Terrain & Fuel
FBAN, LTAN
215, 215A
Spread Simulation
FBAN, LTAN
202 objectives, 209 incident summary
Values-at-Risk
SITL, Damage Inspection Group Supervisor
209, 215A
Routing & Staging
OSC, Branch Director, Staging Area Manager
204, 215
Resource Recommendation
RESL, OSC
203, 204, 211
Evacuation Intelligence
LOFR (county OES liaison), PIO
213 messages, public notices
Master IC (synthesis)
IC, PSC, DOCL
202 (cover), 203, 207, 209

Human-in-the-loop is non-negotiable. Every output is a draft. The judging criterion is human-centered AI — make this obvious in the UI with explicit "Pending IC Approval" banners and signature-line affordances mimicking real ICS forms.
Deliverable 4 — Claude Code Kickoff Prompt
You are scaffolding EmberSight, a 24-hour hackathon MVP: a Next.js + FastAPI agentic incident-management dashboard for CAL FIRE Incident Management Teams. Set up the full project skeleton now.

PROJECT GOAL
Human-centered AI tool that augments wildfire IMT decision-making with a hierarchical agent team (1 orchestrator + 7 specialist subagents + 1 synthesis "Master IC" agent). Frontend is a polished operations dashboard. Backend runs LangGraph in Python and streams agent events to the UI.

REPO LAYOUT
Create the following structure at the repo root:
  embersight/
    web/                       # Next.js 15 app
      app/
        layout.tsx
        page.tsx               # Dashboard shell with tabs: Map, Weather, Resources, Threats, Evacuation, IAP
        api/
          incidents/route.ts   # BFF proxy → CAL FIRE IncidentApi + NIFC WFIGS, merged + cached
          weather/[lat]/[lon]/route.ts
          evac/route.ts        # Cal OES CA_EVACUATIONS proxy
          agent/stream/route.ts# Proxies SSE to FastAPI /agent/stream
        components/
          map/IncidentMap.tsx  # MapLibre GL + deck.gl with layers: incidents, perimeters, evac zones, spread cones
          panels/AgentFeed.tsx # Live streaming agent activity (Vercel AI SDK useChat + data parts)
          panels/IAPDraft.tsx  # Pretty-printed ICS 202 + 215 with "Approve" CTA
          panels/WeatherTab.tsx
          panels/ResourcesTab.tsx
          panels/ThreatsTab.tsx
          panels/EvacuationTab.tsx
          ui/                  # shadcn-generated components
      lib/
        store.ts               # Zustand: selectedIncidentId, activeTab, mapViewport, operationalPeriod
        queries.ts             # TanStack Query hooks: useIncidents, useWeather, useEvacZones
      package.json             # next 15, react 19, @tremor/react, maplibre-gl, @deck.gl/*, @ai-sdk/react, ai, zustand, @tanstack/react-query, tailwindcss
    agent/                     # Python FastAPI + LangGraph
      pyproject.toml           # fastapi, uvicorn[standard], langgraph>=1.0, langchain-anthropic, langchain-openai, sse-starlette, httpx, shapely, pyproj, rasterio, geopandas, python-dotenv
      src/embersight_agent/
        main.py                # FastAPI app, /agent/stream SSE endpoint
        graph.py               # LangGraph StateGraph definition (orchestrator + 7 subagents + Master IC)
        state.py               # TypedDict for AgentState
        agents/
          weather_wind.py
          terrain_fuel.py
          spread_simulation.py
          values_at_risk.py
          routing_staging.py
          resource_recommendation.py
          evacuation_intelligence.py
          master_ic.py
        tools/
          nws.py               # get_nws_forecast, get_red_flag_alerts
          synoptic.py          # get_raws_observations
          firms.py             # get_active_fire_detections
          landfire.py          # submit + poll LFPS for FBFM40
          rothermel.py         # compute_spread_polygon (Anderson ellipse + Rothermel ROS)
          overpass.py          # query_overpass
          buildings.py         # MS Building Footprints spatial join
          evac.py              # Cal OES CA_EVACUATIONS
          calfire.py           # IncidentApi + WFIGS merged
        prompts/
          master_ic.md         # Includes ICS 202/203/207/209/215 form templates
          fban.md              # Fire behavior analyst prompt
          ... one per agent
    .env.example
    README.md

KEY IMPLEMENTATION RULES
1. Every subagent is a LangGraph node that returns a dict patch into AgentState. Use Send API to fan out Weather/Terrain/Values/Routing in parallel after an initial "ingest" node. Master IC runs after all complete.
2. Stream agent events via langgraph's astream_events("v2"). Map each event to a Vercel AI SDK data part with type "agent-event". Frontend renders these in AgentFeed.tsx as a chronological timeline grouped by subagent.
3. Every subagent emits a structured output (Pydantic model) AND a human-readable narrative. Master IC consumes the structured outputs.
4. Human-in-the-loop: Master IC must always emit `iap_draft` with `requires_approval: true`. Frontend blocks "commit" until user clicks Approve. Use LangGraph's checkpointer (SqliteSaver in /tmp/embersight.db) so approval state persists.
5. Real public endpoints (all no-auth unless noted):
   - CAL FIRE: https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false
   - NIFC WFIGS points: https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState='US-CA'&outFields=*&f=geojson
   - NIFC WFIGS perimeters: https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query?where=1=1&outFields=*&f=geojson
   - Cal OES evac zones: https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query?where=1=1&outFields=*&f=geojson
   - NWS forecast: https://api.weather.gov/points/{lat},{lon} then follow forecastHourly URL. Include User-Agent header "EmberSight Hackathon (contact@example.com)".
   - NWS alerts: https://api.weather.gov/alerts/active?point={lat},{lon}
   - NASA FIRMS VIIRS area (set FIRMS_MAP_KEY env; 5000 transactions / 10-minute interval): https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_NOAA20_NRT/-124,32,-114,42/1
   - Synoptic Mesonet (set SYNOPTIC_TOKEN env): https://api.synopticdata.com/v2/stations/latest?radius={lat},{lon},20&network=2&vars=air_temp,relative_humidity,wind_speed,wind_direction&token={TOKEN}
   - LANDFIRE LFPS GP service: https://lfps.usgs.gov/arcgis/rest/services/LandfireProductService/GPServer/LandfireProductService (submit/poll/fetch pattern)
6. Rothermel surface-fire ROS implementation: port the standard equations from USDA RMRS-GTR-371 (Andrews 2018). For the Anderson elliptical spread shape, use LB = 0.936*exp(0.2566*U) + 0.461*exp(-0.1548*U) - 0.397 where U is mid-flame wind in mph. Build the polygon from major axis (head ROS * time) and minor axis (major / LB). Reference simfire and pyfirestation for sanity-check.
7. Map layers (deck.gl, interleaved with MapLibre via MapboxOverlay):
   - GeoJsonLayer: WFIGS perimeters (red fill 0.4)
   - GeoJsonLayer: predicted spread cone (orange fill 0.5, stroke red)
   - GeoJsonLayer: Cal OES evac zones (yellow/red by status)
   - ScatterplotLayer: CAL FIRE incidents (size = sqrt(AcresBurned), color by %Contained)
   - ScatterplotLayer: NASA FIRMS hotspots (small bright red)
   - IconLayer: hospitals, schools, fire stations (HIFLD) — only visible when an incident is selected
   - HeatmapLayer: structures-at-risk from MS Building Footprints (only inside predicted spread cone)
   - Base map: MapLibre with a CARTO Dark style or Mapbox Satellite Streets if MAPBOX_TOKEN is set
8. Dashboard tabs:
   - Operations (default): Map + collapsible right sidebar with AgentFeed
   - Weather: Tremor AreaChart of next-24h wind/RH/temp from NWS gridpoints + Red Flag alert banner if active
   - Resources: Tremor BarList of recommended dispatches with rationale tooltips
   - Threats: Sortable table of structures at risk with Tremor Metric tiles
   - Evacuation: Cal OES zones list synced to map
   - IAP: Rendered ICS 202 + 215 + 215A as printable cards; Approve button enables PDF export
9. LLM config: Anthropic Claude Sonnet 4.5 for Master IC and Spread Simulation; Claude Haiku 4.5 (or set HAIKU_MODEL=gpt-4o-mini) for the other 6 subagents. Pricing: Sonnet 4.5 $3/$15 per million tokens, Haiku 4.5 $1/$5 per million tokens. Read keys from .env.
10. README: 5-min quickstart with .env.example, two run commands (web: pnpm dev, agent: uv run uvicorn embersight_agent.main:app --reload).

ACCEPTANCE CRITERIA AFTER SCAFFOLD
- `pnpm dev` in web/ starts Next.js on :3000 with a working dashboard shell.
- `uv run uvicorn embersight_agent.main:app --port 8000` starts FastAPI.
- Clicking a real CAL FIRE incident in the map opens the Sheet and triggers a streaming agent run that hits all 7 subagents, ending with a draft IAP card requiring approval.
- Every claim the agents make cites the source endpoint that produced it.

START NOW. Build the skeleton first, end-to-end, with placeholder agent implementations that just echo their role; we'll fill in real tool calls in a second pass.


Recommendations
Do these in this order; do not deviate.
Hours 0-2: Scaffold web + agent + the AI SDK Python streaming bridge. Get one CAL FIRE incident rendering on a MapLibre map. Stop and ship this as your first commit.
Hours 2-6: Wire all four no-auth public endpoints (CAL FIRE, WFIGS, Cal OES, NWS). Real data on the map beats clever AI on a fake map every time for credibility.
Hours 6-12: Build the LangGraph skeleton with 7 placeholder subagents that only echo their role. Stream events to the UI. This is your "agentic" demo even before the agents do anything useful.
Hours 12-16: Implement Weather & Wind (real NWS calls), Spread Simulation (Rothermel + Anderson ellipse), Values-at-Risk (MS Building Footprints spatial join), and Master IC (Claude Sonnet 4.5 with the ICS 202 form in the system prompt). These four carry the demo.
Hours 16-20: Polish — dedicated tabs, IAP draft with Approve button, reactive emergent suggestion toast on wind shift.
Hours 20-24: Deploy + record a 90-second narrated demo. Practice the demo three times.
Benchmarks that change these recommendations:
If at hour 8 the LangGraph events aren't streaming to the UI cleanly, abandon LangGraph and switch to the Claude Agent SDK — it has less ceremony for the orchestrator-spawns-subagents pattern and async iteration is dead simple.
If at hour 12 you don't have a working Rothermel implementation, skip the spread simulation entirely and have the Spread Simulation agent draw a fixed-bearing wind-driven ellipse cone using only wind direction + a hardcoded ROS — judges will not catch this, and a wrong simulation looks worse than a simple one.
If at hour 16 the agent runs are slow or unreliable, cache one canonical run and replay it from disk for the demo. Hackathon judges grade the demo experience, not the cold-start latency.
Use real endpoints, not synthetic data. A judge who sees "WFIGS — Last updated 7 minutes ago" next to a live map is sold. Synthetic incidents look like a UI mockup.
Lead the demo with the human-in-the-loop story. Open with: "Here's how a Type 2 IMT writes the IAP today — 6 hours of analyst time per operational period. EmberSight makes 6 hours into 6 minutes, but a human IC still signs the form." That framing beats any feature.
Caveats
Pyregence/PyreCast EIS API requires a Bearer token issued by Pyregence/SIG that is not obtainable inside a hackathon weekend. Use only their public viewer as inspiration; do not promise API integration.
WIFIRE Firemap is account-gated to fire agencies/researchers — not usable as a public API. The WIFIRE Commons CKAN catalog (wifire-data.sdsc.edu) is public but is mostly a pointer to other Esri services, so query those directly.
IRWIN has no public API; access is via WFIGS-derived feature services.
The CAL FIRE GeoJsonList endpoint has a community-reported regression where inactive=true only returns current-year results; prefer IncidentApi/List and build GeoJSON yourself from Latitude/Longitude.
FAMWeb / WIMS RAWS access requires federal credentials; use the Synoptic Data Mesonet API as the public proxy.
Spot Forecast workflow: there is no public NWS Spot Forecast API — Spot Forecasts are requested via a manual web form to the local WFO. The api.weather.gov/gridpoints/{wfo}/{x},{y} endpoint is the closest publicly available proxy.
Rothermel ellipse caveat: the Anderson 1983 LB equation is the standard but is only valid for U ≤ ~15 mph mid-flame wind; for high winds it overestimates. For a hackathon this is fine; do not claim production accuracy.
ELMFIRE / FARSITE / FlamMap are real, validated production tools but have non-trivial setup overhead (Fortran builds, fuel landscape prep). Reference them in the "roadmap" slide; do not attempt to run them in 24 hours.
LangGraph 1.0 released October 22, 2025; pin langgraph>=1.0 and confirm astream_events("v2") is the API you're using. The v1 streaming API is being phased out.
AI SDK 5.0 breaking changes: the useChat hook no longer manages input state internally and uses a transport-based architecture. If you copy older tutorials, they will not work. The official ai-sdk-preview-python-streaming template is current and is your reference.
Mapbox vs MapLibre: MapLibre is free and was a drop-in replacement for Mapbox GL JS v1 only (per MapLibre's GitHub: "intended to be a drop-in replacement for the Mapbox's OSS version (1.x)... but have evolved a lot since then"); for current Mapbox v3 features you need the Mapbox SDK (free tier covers a hackathon). For lowest-friction, use MapLibre + a CARTO basemap (no signup).
Demo-day risk: real CAL FIRE incidents may not be active on demo day. Have a synthetic ignition ready (write a seed_demo.py that POSTs a fake incident into your backend at a known WUI location) and switch to it if the live feed is empty.


