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
  agent/    FastAPI + LangGraph agent service (Python 3.11+)
```

## 5-minute quickstart

### Prerequisites
- Node 20+ and `pnpm`
- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv)
- An Anthropic API key

### 1. Configure environment
```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY at minimum
```

### 2. Run both services (one command)
```bash
./start.sh
```

This installs deps for both `agent/` and `web/`, then boots the FastAPI
agent service on **:8000** and the Next.js web app on **:3000**. Ctrl-C
stops both. Logs stream to `.run/agent.log` and `.run/web.log`.

Open http://localhost:3000.

### Manual two-terminal alternative
If you'd rather run them separately:

```bash
# terminal A
cd agent && uv sync && uv run uvicorn embersight_agent.main:app --reload --port 8000

# terminal B
cd web && pnpm install && pnpm dev
```

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
