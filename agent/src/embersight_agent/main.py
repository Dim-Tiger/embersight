"""FastAPI surface for the EmberSight agent service.

Exposes:
  GET  /healthz
  POST /agent/stream     -> Server-Sent Events of agent activity
  POST /agent/resume     -> resume a paused interrupt with Command(resume=...)
  GET  /agent/pending    -> list checkpoints currently sitting on an interrupt

Events emitted on /agent/stream follow the Vercel AI SDK "data part" shape:
  data: {"type":"agent-event", "value": {...}}\\n\\n
Plus framing events: start, interrupt_pending, done, error, and
`dialogue` events that surface the synthesized orchestrator <-> subagent
conversation alongside the raw LangGraph events.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .graph import compiled_graph
from .state import AgentState, Incident

load_dotenv()
log = logging.getLogger("embersight")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="EmberSight Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class StreamRequest(BaseModel):
    incident: Incident
    operational_period: int = 1
    user_query: str = ""
    thread_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ResumeRequest(BaseModel):
    thread_id: str
    decision: dict[str, Any]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "checkpoint_db": os.environ.get(
            "EMBERSIGHT_CHECKPOINT_DB", "/tmp/embersight.db"
        ),
    }


def _sse(event: str, payload: Any) -> dict:
    return {"event": event, "data": json.dumps(payload, default=str)}


# Short, human-readable task descriptions for each subagent. Surfaced as
# synthesized "Orchestrator -> <agent>" dialogue messages when the
# corresponding LangGraph node starts.
AGENT_TASK_DESCRIPTIONS: dict[str, str] = {
    "weather_wind": (
        "Analyze 24h fire weather (HRRR + RTMA + RAWS + NWS alerts) and "
        "compute a Red Flag flag plus critical window."
    ),
    "terrain_fuel": (
        "Pull terrain (slope/aspect) and fuel beds (LANDFIRE FBFM40) "
        "across the projected AOI."
    ),
    "values_at_risk": (
        "Identify structures, schools, hospitals, and critical "
        "infrastructure inside the projected spread cone."
    ),
    "routing_staging": (
        "Find roads, staging candidates, and water sources within reach "
        "of the incident."
    ),
    "spread_simulation": (
        "Run a wind-and-fuel-driven spread model and produce 1/6/12/24h "
        "spread cones."
    ),
    "resource_recommendation": (
        "Recommend engines, hand crews, dozers, and aviation based on "
        "spread + values. PROPOSE only — IC must approve."
    ),
    "evacuation_intelligence": (
        "Propose evacuation phasing and route-status changes for zones "
        "intersecting the spread cone. PROPOSE only."
    ),
    "master_ic": (
        "Synthesize all subagent outputs into a draft ICS 201/202/204 "
        "and pause for IC approval."
    ),
}


def _dialogue_event(
    from_: str,
    to: str,
    text: str,
    *,
    kind: str = "message",
    extra: dict | None = None,
) -> dict:
    """Build a payload for the `dialogue` SSE event.

    The frontend renders these as a chat-style transcript between the
    orchestrator and each subagent. `kind` is "request" / "response" /
    "thinking" so the UI can style them differently.
    """
    payload: dict = {"from": from_, "to": to, "text": text, "kind": kind}
    if extra:
        payload.update(extra)
    return payload


def _extract_agent_output_from_chain_end(
    name: str, data: Any
) -> dict | None:
    """Pull the AgentOutput dict for `name` from an on_chain_end event."""
    if not isinstance(data, dict):
        return None
    output = data.get("output")
    if not isinstance(output, dict):
        return None
    outputs = output.get("outputs")
    if not isinstance(outputs, dict):
        return None
    ao = outputs.get(name)
    if isinstance(ao, dict):
        return ao
    # Pydantic model — coerce via _safe
    safe = _safe(ao)
    return safe if isinstance(safe, dict) else None


async def _run_and_stream(
    initial_state_or_command: AgentState | Command,
    thread_id: str,
) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph() as graph:
        yield _sse("start", {"thread_id": thread_id})
        # Once-only "orchestrator kicks off the team" dialogue marker so the
        # UI has a deterministic first message.
        yield _sse(
            "dialogue",
            _dialogue_event(
                from_="orchestrator",
                to="team",
                text=(
                    "Kicking off analysis. Fanning out to the seven "
                    "specialist subagents."
                ),
                kind="kickoff",
            ),
        )
        try:
            async for event in graph.astream_events(
                initial_state_or_command, config=config, version="v2"
            ):
                # Forward only the high-signal events. LangGraph emits a lot
                # of low-level LLM-token events too; the UI consumes
                # node-start/end + custom data emissions.
                kind = event.get("event", "")
                name = event.get("name")
                metadata = event.get("metadata") or {}
                langgraph_node = metadata.get("langgraph_node")

                if kind in (
                    "on_chain_start",
                    "on_chain_end",
                    "on_chat_model_stream",
                    "on_custom",
                ):
                    yield _sse(
                        "agent-event",
                        {
                            "kind": kind,
                            "name": name,
                            "data": _safe(event.get("data")),
                            "run_id": event.get("run_id"),
                            "tags": event.get("tags", []),
                            "langgraph_node": langgraph_node,
                        },
                    )

                # ---- Synthesize dialogue at agent boundaries ---------------
                if (
                    kind == "on_chain_start"
                    and isinstance(name, str)
                    and name in AGENT_TASK_DESCRIPTIONS
                ):
                    yield _sse(
                        "dialogue",
                        _dialogue_event(
                            from_="orchestrator",
                            to=name,
                            text=AGENT_TASK_DESCRIPTIONS[name],
                            kind="request",
                        ),
                    )
                elif (
                    kind == "on_chain_end"
                    and isinstance(name, str)
                    and name in AGENT_TASK_DESCRIPTIONS
                ):
                    ao = _extract_agent_output_from_chain_end(
                        name, event.get("data")
                    )
                    if ao:
                        narrative = ao.get("narrative") or ""
                        conf = ao.get("confidence")
                        yield _sse(
                            "dialogue",
                            _dialogue_event(
                                from_=name,
                                to="orchestrator",
                                text=narrative,
                                kind="response",
                                extra={
                                    "confidence": conf,
                                    "confidence_driver": ao.get(
                                        "confidence_driver"
                                    ),
                                },
                            ),
                        )

            # After astream_events drains, check whether the graph paused
            # on an interrupt. If yes, surface it so the UI can present an
            # approval card.
            snapshot = await graph.aget_state(config)
            pending = _extract_pending_interrupt(snapshot)
            if pending is not None:
                yield _sse(
                    "interrupt_pending",
                    {"thread_id": thread_id, "interrupt": pending},
                )
            yield _sse("done", {"thread_id": thread_id})
        except Exception as exc:  # noqa: BLE001
            log.exception("agent stream error")
            yield _sse("error", {"message": str(exc)})


def _safe(value: Any) -> Any:
    """Best-effort JSON-safe coercion (Pydantic models → dicts, recursive)."""
    from pydantic import BaseModel as _BaseModel  # local import avoids circulars
    if isinstance(value, _BaseModel):
        return _safe(value.model_dump())
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    try:
        import json as _json
        _json.dumps(value)
        return value
    except Exception:  # noqa: BLE001
        return str(value)


def _extract_pending_interrupt(snapshot: Any) -> dict | None:
    tasks = getattr(snapshot, "tasks", None) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or []
        for itr in interrupts:
            value = getattr(itr, "value", None)
            if value is not None:
                return value if isinstance(value, dict) else {"value": value}
    return None


@app.post("/agent/stream")
async def agent_stream(req: StreamRequest) -> EventSourceResponse:
    initial = AgentState(
        incident=req.incident,
        operational_period=req.operational_period,
        user_query=req.user_query,
    )
    return EventSourceResponse(_run_and_stream(initial, req.thread_id))


@app.post("/agent/resume")
async def agent_resume(req: ResumeRequest) -> EventSourceResponse:
    cmd = Command(resume=req.decision)
    return EventSourceResponse(_run_and_stream(cmd, req.thread_id))


@app.get("/agent/pending/{thread_id}")
async def agent_pending(thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph() as graph:
        snapshot = await graph.aget_state(config)
        pending = _extract_pending_interrupt(snapshot)
        if pending is None:
            raise HTTPException(status_code=404, detail="no pending interrupt")
        return {"thread_id": thread_id, "interrupt": pending}
