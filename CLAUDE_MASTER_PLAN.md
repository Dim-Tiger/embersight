# EmberSight: Conversational AI Incident Management Team

## TL;DR

EmberSight is a Next.js + Python (FastAPI + LangGraph) application that gives a human Incident Management Team a peer counterpart: a conversational AI Incident Commander who commands a virtual AI IMT of seven specialist agents (Weather & Wind, Terrain & Fuel, Spread Simulation, Values-at-Risk, Routing & Staging, Resource Recommendation, Evacuation Intelligence). The human IC talks to one entity — the AI Master IC — and the AI IC briefs themselves with their team and delegates targeted questions in the background. The product feel is *one intelligent peer*, not eight parallel readouts.

The system runs in two modes:

1. **Initial briefing** — when an incident is selected, the AI Master IC runs a fan-out across all seven subagents in parallel to build a complete operating picture. This populates every dashboard tab (Weather, Threats, Resources, Evacuation, IAP) and produces a draft IAP awaiting IC approval.
2. **Conversational mode** — after the briefing, every user message routes to the AI Master IC alone. The IC reads the cached state of every subagent's last output, answers from intelligence already on hand when possible, and *selectively* re-invokes one or two specialists as tools when fresh data is needed ("wind shifted south, re-consult Spread Simulation"). The delegation is visible in the chat — the human sees the IC say *"consulting Weather & Wind…"* — but they're never in a conversation with the subagent directly.

Human-in-the-loop hits three explicit choke points: (1) IAP approval before commitment to an operational period, (2) any change in evacuation-zone status the IC drafts, and (3) any resource recommendation that materially escalates posture. Every dashboard line is a draft. EmberSight never dispatches.

---

## Architectural model

```
┌──────────────────── Human IC ────────────────────┐
│   one conversational thread + one approval loop  │
└─────────────────────┬────────────────────────────┘
                      │
                      ▼
            ┌──────────────────┐
            │   AI Master IC   │   <- single conversational peer
            │  (Sonnet 4.5)    │      reads/writes shared state
            └──────────┬───────┘      orchestrates the team
                       │
       ┌───────────────┴──────────────┐
       │     subagent toolbelt        │   <- AI section chiefs / specialists
       │  (Haiku 4.5 each, called     │      called selectively as tools
       │   only when IC asks)         │      by the IC, never by the human
       ├──────────────────────────────┤
       │  Weather & Wind     [FBAN]   │
       │  Terrain & Fuel     [LTAN]   │
       │  Spread Simulation  [FBAN]   │
       │  Values at Risk     [SITL]   │
       │  Routing & Staging  [OSC]    │
       │  Resource Rec.      [RESL]   │
       │  Evacuation Intel   [LOFR]   │
       └──────────────────────────────┘
```

### Two graph entry points (same checkpointed thread)

- **`mode=briefing`**: a one-shot DAG that fans out to all seven specialists in parallel, then the Master IC node synthesizes a draft IAP and interrupts for human approval. Same topology as a Type 3 IMT's first operational period: every section chief reports up; IC owns the IAP.
- **`mode=chat`**: a small loop — Master IC reads the latest user message + the cached `outputs` map + the conversation history, decides whether to answer directly or call one or more subagent tools, optionally loops once more after tool results, and returns a single reply. No fan-out, no rerun of the whole team.

Both modes share the same LangGraph state, the same SQLite checkpointer, the same `thread_id`, and the same shared `outputs` cache. A chat turn that re-consults Spread Simulation overwrites only that one entry; everything else stays warm.

### Subagents as tools

Each of the seven specialist agents exposes a tool to the Master IC with a tight signature:

```python
@tool
async def consult_weather_wind(
    question: str,
    must_refresh: bool = False,
) -> WeatherWindOutput:
    """Get the latest fire-weather brief. Set must_refresh=True if you
    need to pull fresh NWS/HRRR/RAWS data; otherwise the cached output
    from the initial briefing is used."""
```

The IC chooses which to call based on the question. The tool either returns the cached output verbatim (cheap path, ~50ms) or re-invokes the subagent's `run(state)` against the live incident (full path, 5-30s). Either way the result is summarized into the IC's next reply.

### Why this is the right shape

- **Mirrors how IMTs work.** Real IC's talk to their section chiefs, not directly to every analyst. The conversational model carries that intent into the AI layer.
- **Removes the "everything runs every time" problem.** The initial briefing is a one-time cost per incident. Subsequent questions get answered from a warm cache plus targeted refreshes.
- **Keeps every subagent output visible.** The dashboard tabs read from the same shared `outputs` map. The chat is conversational; the tabs are reference.
- **Streams the delegation.** When the IC calls a tool, the SSE stream emits a `tool_call_start` / `tool_call_end` pair that the UI surfaces as "consulting Weather & Wind…" in line with the chat. The human sees the team work without having to listen in on the team's internal chatter.

---

## Backend topology (LangGraph)

### State

```python
class AgentState(BaseModel):
    incident: Incident | None
    operational_period: int = 1
    mode: Literal["briefing", "chat"] = "briefing"

    # Conversational history. Master IC reads this in chat mode.
    messages: Annotated[list[BaseMessage], add_messages] = []

    # Cached subagent outputs, keyed by agent name. Survives across
    # turns within a thread.
    outputs: Annotated[dict[str, AgentOutput], _merge_outputs] = {}

    # IAP synthesis + audit
    iap_draft: dict | None = None
    dissent_log: list[dict] = []
    audit_log: list[InterruptRecord] = []
```

### Graphs

```
briefing_graph:
  START → orchestrator → fan_out → [weather_wind, terrain_fuel,
                                    values_at_risk, routing_staging]
                                  ↓
                          [spread_simulation, resource_recommendation,
                           evacuation_intelligence]
                                  ↓
                          master_ic_briefing → interrupt(iap_approval)
                                  ↓
                                 END

chat_graph:
  START → master_ic_chat ─┬─→ answer            → END
                          └─→ tool_node[subagent] → master_ic_chat  (loop ≤2)
```

The `tool_node` is a LangGraph `ToolNode` constructed from the seven `consult_*` tools. Master IC's `chat` node uses `with_tools` and emits a tool call; LangGraph routes through the tool node and back. Two iterations max so a single user turn can't spin forever.

### Endpoint contract

`POST /agent/stream` accepts `{incident, mode, message?, operational_period, thread_id}`:

- `mode="briefing"` triggers the briefing graph. Body is ignored.
- `mode="chat"` triggers the chat graph with `message` appended to `messages`.

Streams the same Vercel AI SDK data-part shapes plus two new framing events:

- `tool_call_start`: `{name, args}` when the IC delegates to a specialist.
- `tool_call_end`: `{name, summary}` when the specialist returns.

### Model + cost

- Master IC: **Claude Sonnet 4.5** (reasoning + tool selection). ≈$3 in / $15 out per million tokens.
- Subagent specialists: **Claude Haiku 4.5**. ≈$1 in / $5 out per million tokens.
- A typical conversational turn that doesn't re-call any tool: ≈1 Sonnet round-trip, ~5K tokens, **~$0.02**.
- A turn that re-calls one specialist: ≈1 Sonnet + 1 Haiku + 1 Sonnet, **~$0.05**.
- The initial briefing is the expensive event (7 Haiku + 1 Sonnet, ~$0.10) and only fires once per incident.

---

## Frontend UX

The right column of Operations becomes a **chat-first** column:

- **Top**: the chat with the AI Master IC. User types, IC responds. When the IC delegates, a small inline card appears in the conversation flow showing which specialist is being consulted and a one-line summary when it returns. The human never types to a specialist directly.
- **Middle**: a compact **team status strip** — nine dots, one per agent, showing pending / running / done state. This is the only place the seven specialists are visible as individual entities.
- **Bottom**: the approval queue. IAP drafts and other interrupts land here.

The left-hand tabs (Weather / Threats / Resources / Evacuation / IAP) become **reference views** rendered from the cached `outputs` map. They're populated by the initial briefing and updated whenever a subagent is re-consulted. They're not part of the conversation; they're the IC's situation room.

### Initial briefing flow

1. User picks an incident from the sidebar.
2. Frontend automatically POSTs `mode=briefing` with the incident.
3. SSE stream fills the team status strip and the chat shows a single "Briefing in progress" turn from the AI Master IC.
4. When the briefing completes, the AI IC posts a synthesis turn ("12-hr outlook: red flag in effect, 2,000 structures in cone, recommend Type 2 IMT escalation, IAP-201 awaiting your approval") and the dashboard tabs are live.
5. From this point on, every user message uses `mode=chat`.

### Conversational turn flow

1. User types a message ("how confident are we in the 12-hr spread?")
2. POST `mode=chat` with `{message, thread_id}`.
3. SSE stream:
   - `agent-event: on_chain_start name=master_ic_chat`
   - `tool_call_start: {name: "consult_spread_simulation", args: {must_refresh: false}}`
   - `tool_call_end: {name: "consult_spread_simulation", summary: "..."}`
   - `agent-event: on_chat_model_stream` (IC's tokens streaming)
   - `agent-event: on_chain_end name=master_ic_chat`
   - `done`
4. UI shows: a "consulting Spread Simulation" pill briefly, then the IC's reply as one chat bubble.

### What disappears

- The seven separate "[agent_name] narrative" chat bubbles that used to fill the chat are gone. The IC speaks for the team. The specialists' raw text only shows up in the reference tabs.
- The "send a message to the team" affordance becomes "ask the IC" — one conversational counterpart, one mental model.

---

## Roles & ICS mapping (unchanged research)

CAL FIRE uses the NWCG position taxonomy under the California Interagency Incident Command System (CICCS). The seven specialist agents map to the technical-specialist + section-chief level so that the AI IMT can mirror a Type 2/Type 3 human IMT.

| EmberSight Agent           | ICS Position(s) Augmented                  | Primary IAP Forms Touched           |
|----------------------------|--------------------------------------------|-------------------------------------|
| Weather & Wind             | FBAN, IMET                                 | ICS 202 weather block, 215          |
| Terrain & Fuel             | FBAN, LTAN                                 | 215, 215A                           |
| Spread Simulation          | FBAN, LTAN                                 | 202 objectives, 209 incident summary |
| Values-at-Risk             | SITL, Damage Inspection Group Supervisor   | 209, 215A                           |
| Routing & Staging          | OSC, Branch Director, Staging Area Mgr     | 204, 215                            |
| Resource Recommendation    | RESL, OSC                                  | 203, 204, 211                       |
| Evacuation Intelligence    | LOFR (county OES liaison), PIO             | 213 messages, public notices        |
| **AI Master IC (synthesis + chat)** | **IC, PSC, DOCL**                  | **202, 203, 207, 209**             |

The Planning P (FEMA's IAP development cycle, formalized in NFPA 1561 Annex C and FEMA IS-200) governs the operational rhythm. The dashboard surfaces an explicit "Operational Period" header with a countdown and a "Generate next-period IAP draft" CTA at hours 10-11 of each period.

---

## Data sources (no-auth where possible)

This section is the same as the prior research pass — these endpoints are what the subagent specialists call when the IC asks them for fresh data.

| Source                                  | Endpoint                                                                                                                                                                            | Format          |
|-----------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------|
| CAL FIRE active incidents               | `https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false`                                                                                                          | JSON array      |
| NIFC WFIGS incident points              | `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState='US-CA'&outFields=*&f=geojson`              | GeoJSON         |
| NIFC WFIGS perimeters                   | `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query?where=1=1&outFields=*&f=geojson`                       | GeoJSON         |
| Cal OES CA_EVACUATIONS (active overlay) | `https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query?where=1=1&outFields=*&f=geojson`                            | GeoJSON         |
| Genasys / Zonehaven WFS (static catalog) | `https://zms.zonehaven.com/geoserver/z/wfs?authkey={county_key}&typeNames=z:evacuation_zone_status_CA&outputFormat=application/json` — per-county public authkeys registered on ArcGIS Hub | GeoJSON         |
| San Mateo County ZoneHaven (static)     | `https://services.arcgis.com/yq3FgOI44hYHAFVZ/arcgis/rest/services/ZoneHaven_SMCEvacuationZones/FeatureServer/0/query?where=1=1&f=geojson`                                            | GeoJSON         |
| NWS hourly forecast                     | `https://api.weather.gov/points/{lat},{lon}` → follow `properties.forecastHourly`                                                                                                    | JSON            |
| NWS alerts (Red Flag, Fire Wx Watch)    | `https://api.weather.gov/alerts/active?point={lat},{lon}`                                                                                                                            | JSON-LD / CAP   |
| Synoptic Mesonet (RAWS)                 | `https://api.synopticdata.com/v2/stations/latest?radius={lat},{lon},20&network=2&vars=...&token={TOKEN}`                                                                             | JSON            |
| LANDFIRE LFPS fuel rasters              | `https://lfps.usgs.gov/arcgis/rest/services/LandfireProductService/GPServer/...`                                                                                                     | GeoTIFF (async) |
| NASA FIRMS active fire (VIIRS)          | `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_NOAA20_NRT/...`                                                                                                       | CSV             |
| OSM Overpass (roads, hydrants)          | `https://overpass-api.de/api/interpreter` with QL query                                                                                                                              | JSON            |
| HIFLD (schools, hospitals, power)       | `https://hifld-geoplatform.opendata.arcgis.com/` (per layer)                                                                                                                         | GeoJSON         |
| MS Building Footprints (USA)            | `https://github.com/microsoft/USBuildingFootprints` per-state GeoJSON                                                                                                                | GeoJSON         |

---

## Fire progression models

The Spread Simulation specialist uses a three-tier fidelity ladder, deepest tier deployed when time permits:

1. **Anderson elliptical spread cone**, parameterized by Rothermel rate of spread. Length-to-breadth ratio `LB = 0.936·exp(0.2566·U) + 0.461·exp(-0.1548·U) - 0.397` where `U` is mid-flame wind speed in mph. Always available — pure math, no external data needed.
2. **Python Rothermel ROS** via `mitrefireline/simfire` (MIT-licensed, runs Rothermel surface model on Scott & Burgan fuel rasters with a pygame visualization) or `PyFireCoimbra/PyFireStation`.
3. **ELMFIRE** — open-source production-grade Fortran spread engine. Significant fuel-prep overhead; flagged as production roadmap rather than demo scope.

Reference: USDA RMRS-GTR-371 (Andrews 2018), the canonical Rothermel + Anderson ellipse text.

---

## Implementation plan (current state)

The repo is split into two services that share a single thread_id:

```
embersight/
  web/                     # Next.js 15 app, chat-primary UX
    app/
      page.tsx             # Operations dashboard shell with reference tabs
      api/
        agent/stream/      # Proxies SSE to FastAPI /agent/stream
        agent/resume/      # Proxies SSE to /agent/resume (HITL)
        incidents/         # BFF for CAL FIRE + WFIGS
        evac/              # BFF for Cal OES
        weather/[lat]/[lon]/  # BFF for NWS alerts
        perimeter/         # BFF for WFIGS perimeters
      components/
        map/IncidentMap.tsx
        panels/
          AgentFeed.tsx        # chat + team status strip (post-redesign)
          IAPDraft.tsx         # structured ICS form viewer
          ApprovalQueue.tsx    # HITL interrupts
          WeatherTab.tsx       # reference view from weather_wind output
          ResourcesTab.tsx     # reference view from resource_recommendation
          ThreatsTab.tsx       # reference view from values_at_risk
          EvacuationTab.tsx    # reference view from evacuation_intelligence
    lib/
      sse.ts                   # CRLF-tolerant SSE consumer
      store.ts                 # Zustand: outputs, statuses, chat, interrupts
      queries.ts               # TanStack Query hooks + postResume

  agent/                    # FastAPI + LangGraph
    src/embersight_agent/
      main.py               # /agent/stream, /agent/resume, /agent/pending
      graph.py              # briefing_graph + chat_graph (same checkpointer)
      state.py              # AgentState (messages + outputs + iap_draft)
      hitl.py               # interrupt envelope + audit
      agents/
        weather_wind.py
        terrain_fuel.py
        spread_simulation.py
        values_at_risk.py
        routing_staging.py
        resource_recommendation.py
        evacuation_intelligence.py
        master_ic.py        # both briefing synthesis AND chat node
        tools.py            # consult_* wrappers exposed to Master IC
      tools/
        nws.py
        synoptic_raws.py
        herbie_wx.py
        landfire.py
        pyretechnics_spread.py
        overpass.py
        infra.py
        buildings.py
        evac.py
        calfire.py
        firms.py
        routing.py
        seed_demo.py
      prompts/
        master_ic.md        # IC personality, ICS form templates, tool guide
        fban.md
        ... one per specialist
```

### Key contract rules

1. **Every subagent returns a typed `AgentOutput`** with `narrative`, `payload`, `confidence`, `citation_bundle`. The `outputs` reducer in `AgentState` merges new outputs without clobbering.
2. **Master IC has two personas in one module**: a `synthesize_briefing(state)` callable used by `briefing_graph`, and a `chat_turn(state)` callable used by `chat_graph`. They share the IC's system prompt and tool toolbox.
3. **Subagent re-invocation through tools always writes back into `state.outputs`**, so the reference tabs reflect the latest data immediately.
4. **HITL interrupts only fire from the briefing path** by default (IAP approval at end of briefing). Chat turns can request an interrupt via a special `request_ic_signoff(...)` tool when they're about to commit something irreversible, but most conversational turns return without pause.
5. **Verb constraint repeated everywhere**: the IC and every specialist use `RECOMMEND / PROPOSE / DRAFT / SUGGEST`. Never `dispatch / order / send / publish`. Only a human IC commits.

### Streaming contract

Frontend reads SSE frames separated by either `\n\n` or `\r\n\r\n` (Next.js's Node-runtime proxy normalizes to CRLF). Each frame is `event: <kind>\ndata: <json>\n\n`. Known kinds:

| event              | payload                                                | UI behavior                                                  |
|--------------------|--------------------------------------------------------|--------------------------------------------------------------|
| `start`            | `{thread_id}`                                          | Set status `consuming`.                                      |
| `agent-event`      | `{kind, name, data, run_id, tags}`                     | Drive the team status strip. `on_chain_end` for an agent name populates `outputs[name]`. |
| `tool_call_start`  | `{name, args}`                                         | Show "consulting <agent>…" pill inline in chat.              |
| `tool_call_end`    | `{name, summary}`                                      | Replace pill with completed summary card.                    |
| `chat_token`       | `{delta}`                                              | Append to current IC chat bubble.                            |
| `interrupt_pending`| `{thread_id, interrupt}`                               | Push into approval queue.                                    |
| `done`             | `{thread_id}`                                          | Mark turn complete.                                          |
| `error`            | `{message}`                                            | Surface in red banner.                                       |

---

## What this design is not

- **It's not autonomous dispatch.** No agent ever sends a unit anywhere. The verb wall (RECOMMEND / PROPOSE / DRAFT / SUGGEST) is enforced in every system prompt and grep-checkable in the codebase.
- **It's not a chat-with-individual-agents UX.** The human only talks to the Master IC. Talking to a specialist directly would break the mental model and create the same "everything fires every time" UX that conversational delegation is designed to fix.
- **It's not a single-shot pipeline.** The earlier draft of this doc described a one-shot IAP-generation flow with no follow-up. That model didn't match how IMTs actually work across multiple operational periods. The conversational model is the upgrade.
- **It's not a production fire-modeling tool.** ELMFIRE / FARSITE / FlamMap are real validated tools but require Fortran builds and serious fuel landscape preparation. EmberSight references them as roadmap; it ships with the Anderson ellipse + Rothermel ROS at MVP fidelity.

---

## Caveats

- **CAL FIRE `GeoJsonList?inactive=true` has a community-reported regression** — prefer the JSON `List` endpoint and build GeoJSON yourself.
- **IRWIN has no public API** — access via WFIGS-derived feature services.
- **FAMWeb / WIMS RAWS access requires federal credentials** — use the Synoptic Data Mesonet API as the practical public proxy.
- **No public NWS Spot Forecast API** — the `gridpoints/{wfo}/{x},{y}` endpoint is the closest publicly available proxy.
- **Anderson 1983 LB equation is valid for U ≤ ~15 mph mid-flame wind** — at high winds it overestimates. Fine for MVP, don't claim production accuracy.
- **Real CAL FIRE incidents may not be active on demo day.** `seed_demo.py` POSTs a synthetic ignition near a known WUI; switch to it if the live feed is empty.

---

## Why this is worth building

The judging criterion is human-centered AI. A conversational AI IMT is the cleanest expression of that: the human IC keeps full authority — they sign every IAP, they approve every resource recommendation, they own every evacuation order — while an AI peer handles the analytical workload that today eats six hours of an analyst's day per operational period. The demo opens with: *"This is how an IC drafts an IAP today — six hours of analyst time. EmberSight reduces that to six minutes, but a human IC still signs the form."*
