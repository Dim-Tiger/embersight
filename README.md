# EmberSight

Human-centered agentic incident-management dashboard for CAL FIRE Incident Management Teams.

EmberSight augments — never replaces — IMT decision-making. A hierarchical LangGraph
agent team (1 orchestrator + 7 specialist subagents + 1 synthesis "Master IC")
produces draft incident-action artifacts. Every actionable artifact pauses
execution on a LangGraph `interrupt()` until a human IC approves, edits, or
rejects it via `Command(resume=...)`.

**EmberSight never dispatches.** It recommends, proposes, and drafts.

## Architecture

```
embersight/
  web/      Next.js 15 + React 19 dashboard (MapLibre + deck.gl + Tremor)
  agent/    FastAPI + LangGraph agent service (Python 3.11+, see agent/README.md)
```

## 5-minute quickstart

### Prerequisites
- Node 20+ and `pnpm`
- [`uv`](https://github.com/astral-sh/uv) (it downloads the right Python for you)
- Optional: an Anthropic API key. Without one every agent still runs and
  writes a deterministic, template-based narrative; the key only switches the
  narratives and the Master IC chat to Claude.

### 1. Configure environment (optional)
```bash
cp .env.example .env            # read by the agent service
cp .env.example web/.env.local  # read by the web app (FIRMS / Mapbox keys)
# fill in ANTHROPIC_API_KEY, FIRMS_MAP_KEY, SYNOPTIC_TOKEN as available
```
Everything has a working default: the web app talks to the agent at
`http://localhost:8000`, and checkpoints go to `/tmp/embersight.db`.

### 2. Start the agent service (port 8000)
```bash
cd agent
uv sync --python 3.12
uv run uvicorn embersight_agent.main:app --reload --port 8000
curl localhost:8000/healthz   # {"ok": true, ...}
```

Python 3.11 through 3.14 all work for the default install. The optional
`science` group (HRRR/RTMA via Herbie, RAWS via SynopticPy, OSMnx road
routing, LANDFIRE helpers) needs Python 3.11 or 3.12, because `pyretechnics`
pins `scipy~=1.12` and `numpy~=1.26`:

```bash
uv sync --python 3.12 --group science
```

Without it, weather falls back to the NWS hourly forecast and routing skips
drive-time routes; each agent says so in its narrative and confidence.

### 3. Run the tests (offline, about 5 seconds)
```bash
cd agent
uv run pytest -q
```
The suite blocks all network access, so it needs no keys and no live feeds.

### 4. Start the web app (port 3000)
```bash
cd web
pnpm install
pnpm dev
```

Open http://localhost:3000.

## How human-in-the-loop works

1. A user selects a CAL FIRE incident on the map.
2. The web app opens an SSE stream to `/agent/stream`, which runs a LangGraph
   `StateGraph`: orchestrator → parallel (Weather, Terrain, Values, Routing) →
   Spread Simulation → (Resource Rec, Evac Intel) → Master IC.
3. Master IC, plus any subagent crossing a "trigger point" (spread breaching a
   threshold, recommended apparatus dispatch, evac zone change), calls
   `interrupt(...)`. The graph pauses.
4. The frontend `ApprovalQueue` surfaces the pending decision with the draft
   artifact, confidence score, and citation bundle. The user picks Approve /
   Edit / Reject.
5. The web app POSTs the decision to `/agent/resume`, which calls
   `graph.invoke(Command(resume={"decision": ...}), config)`. The graph
   continues from the exact interrupt point.

State is persisted in `SqliteSaver` at `$EMBERSIGHT_CHECKPOINT_DB` (default
`/tmp/embersight.db`). Killing and restarting the agent service does not lose a
pending interrupt.

## Demo Day Fallback

If no real California fires are active during the demo, seed a synthetic
ignition:

```bash
cd agent
uv run python -m embersight_agent.tools.seed_demo
```

This injects a fake incident centered in the Los Padres NF that the orchestrator
will treat exactly like a real CAL FIRE incident.

## Repo guarantees

- The Resource Recommendation agent has **no tool** named `dispatch_*`,
  `order_*`, `send_*`, or `publish_evacuation_*`.
- Every state mutation that could become an external action passes through
  `interrupt()`.
- Every interrupt → decision pair is appended to the `audit_log` table.
- All UI verbs for action artifacts read **RECOMMEND** / **PROPOSED** /
  **DRAFT**.
