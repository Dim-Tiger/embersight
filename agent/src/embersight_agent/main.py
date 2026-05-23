"""FastAPI surface for the EmberSight agent service.

Exposes:
  GET  /healthz
  POST /agent/stream     -> Server-Sent Events of agent activity
  POST /agent/resume     -> resume a paused interrupt with Command(resume=...)
  GET  /agent/pending    -> list checkpoints currently sitting on an interrupt

Events emitted on /agent/stream follow the Vercel AI SDK "data part" shape:
  data: {"type":"agent-event", "value": {...}}\\n\\n
Plus a few framing events: start, interrupt_pending, done, error.
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


async def _run_and_stream(
    initial_state_or_command: AgentState | Command,
    thread_id: str,
) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph() as graph:
        yield _sse("start", {"thread_id": thread_id})
        try:
            async for event in graph.astream_events(
                initial_state_or_command, config=config, version="v2"
            ):
                # Forward only the high-signal events. LangGraph emits a lot
                # of low-level LLM-token events too; the UI consumes
                # node-start/end + custom data emissions.
                kind = event.get("event", "")
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
                            "name": event.get("name"),
                            "data": _safe(event.get("data")),
                            "run_id": event.get("run_id"),
                            "tags": event.get("tags", []),
                        },
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
