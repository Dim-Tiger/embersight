"""FastAPI surface for the EmberSight agent service.

Exposes:
  GET  /healthz
  POST /agent/stream     -> SSE: briefing OR chat run (selected via `mode`)
  POST /agent/resume     -> SSE: resume a paused interrupt with Command(resume=...)
  GET  /agent/pending/{thread_id} -> peek at a paused interrupt

Events emitted on /agent/stream follow the Vercel AI SDK "data part" shape:
  data: {"type":"agent-event", "value": {...}}\\n\\n
Framing events: start, tool_call_start, tool_call_end, chat_token,
interrupt_pending, done, error.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .graph import compiled_graph
from .state import AgentState, Incident, TestOverrides

load_dotenv()
log = logging.getLogger("embersight")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="EmberSight Agent", version="0.2.0")

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
    mode: Literal["briefing", "chat"] = "briefing"
    message: str = ""  # used in chat mode
    operational_period: int = 1
    user_query: str = ""  # legacy, kept for back-compat with the old client
    thread_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Dev-panel synthetic-data overrides. Injected by the Next.js proxy when
    # the user has test mode enabled, so the agent's tools (weather_wind, ...)
    # operate on the synthetic conditions rather than the real upstream feeds.
    test_overrides: TestOverrides | None = None


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


async def _stream_briefing(
    initial_state: AgentState | Command,
    thread_id: str,
) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph(mode="briefing") as graph:
        yield _sse("start", {"thread_id": thread_id, "mode": "briefing"})
        try:
            async for event in graph.astream_events(
                initial_state, config=config, version="v2"
            ):
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

            snapshot = await graph.aget_state(config)
            pending = _extract_pending_interrupt(snapshot)
            if pending is not None:
                yield _sse(
                    "interrupt_pending",
                    {"thread_id": thread_id, "interrupt": pending},
                )
            yield _sse("done", {"thread_id": thread_id, "mode": "briefing"})
        except Exception as exc:  # noqa: BLE001
            log.exception("briefing stream error")
            yield _sse("error", {"message": str(exc)})


async def _stream_chat(
    state_patch: dict[str, Any],
    thread_id: str,
) -> AsyncIterator[dict]:
    """Run one chat turn. The patch contains the new HumanMessage plus
    enough state metadata (incident etc.) for the master_ic_chat node."""
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph(mode="chat") as graph:
        yield _sse("start", {"thread_id": thread_id, "mode": "chat"})
        try:
            async for event in graph.astream_events(
                state_patch, config=config, version="v2"
            ):
                kind = event.get("event", "")
                name = event.get("name")
                data = event.get("data") or {}

                if kind == "on_tool_start" and name and name.startswith("consult_"):
                    yield _sse(
                        "tool_call_start",
                        {
                            "name": name,
                            "args": _safe(data.get("input")),
                            "run_id": event.get("run_id"),
                        },
                    )
                elif kind == "on_tool_end" and name and name.startswith("consult_"):
                    output = _safe(data.get("output"))
                    yield _sse(
                        "tool_call_end",
                        {
                            "name": name,
                            "summary": output,
                            "run_id": event.get("run_id"),
                        },
                    )
                elif kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    delta = ""
                    if chunk is not None:
                        c = getattr(chunk, "content", "")
                        if isinstance(c, list):
                            # Anthropic streams content as a list of blocks
                            delta = "".join(
                                (b.get("text", "") if isinstance(b, dict) else "")
                                for b in c
                            )
                        else:
                            delta = c if isinstance(c, str) else str(c)
                    if delta:
                        yield _sse(
                            "chat_token",
                            {"delta": delta, "run_id": event.get("run_id")},
                        )
                elif kind in ("on_chain_start", "on_chain_end"):
                    yield _sse(
                        "agent-event",
                        {
                            "kind": kind,
                            "name": name,
                            "data": _safe(data),
                            "run_id": event.get("run_id"),
                            "tags": event.get("tags", []),
                        },
                    )

            snapshot = await graph.aget_state(config)
            pending = _extract_pending_interrupt(snapshot)
            if pending is not None:
                yield _sse(
                    "interrupt_pending",
                    {"thread_id": thread_id, "interrupt": pending},
                )
            yield _sse("done", {"thread_id": thread_id, "mode": "chat"})
        except Exception as exc:  # noqa: BLE001
            log.exception("chat stream error")
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
    if req.mode == "briefing":
        initial = AgentState(
            incident=req.incident,
            operational_period=req.operational_period,
            mode="briefing",
            user_query=req.user_query,
            test_overrides=req.test_overrides,
        )
        return EventSourceResponse(_stream_briefing(initial, req.thread_id))

    # chat mode
    text = (req.message or req.user_query or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message required in chat mode")
    patch: dict[str, Any] = {
        "messages": [HumanMessage(content=text)],
        "incident": req.incident,
        "operational_period": req.operational_period,
        "mode": "chat",
        "test_overrides": req.test_overrides,
    }
    return EventSourceResponse(_stream_chat(patch, req.thread_id))


@app.post("/agent/resume")
async def agent_resume(req: ResumeRequest) -> EventSourceResponse:
    cmd = Command(resume=req.decision)
    # Resume against whichever graph paused on the interrupt. Interrupts
    # can come from EITHER the briefing graph (the original evac_intel
    # status-change proposals) or the chat graph (the synthetic test
    # path added when the IC was wired to direct specialists). They
    # share a checkpointer, but each compiled graph only knows how to
    # resume a node that exists in its own topology — invoking the
    # wrong graph leaves the paused state untouched.
    #
    # Source of truth: `scratch.mode` set by the orchestrator (briefing)
    # or the chat node (chat). We read it from the checkpoint snapshot
    # and route the resume accordingly. Default = briefing for
    # backwards compatibility.
    paused_mode = await _detect_paused_mode(req.thread_id)
    if paused_mode == "chat":
        return EventSourceResponse(_stream_chat(cmd, req.thread_id))
    return EventSourceResponse(_stream_briefing(cmd, req.thread_id))


async def _detect_paused_mode(thread_id: str) -> str:
    """Inspect the checkpointed state for thread_id and return whichever
    mode ('briefing' or 'chat') is currently paused on an interrupt.
    Defaults to 'briefing' if we can't tell."""
    config = {"configurable": {"thread_id": thread_id}}
    # Try the chat graph first — if a chat-mode interrupt paused there,
    # this snapshot will show next steps. If chat isn't paused, fall
    # through to briefing.
    try:
        async with compiled_graph(mode="chat") as graph:
            snap = await graph.aget_state(config)
            scratch = (snap.values or {}).get("scratch") or {}
            if scratch.get("mode") == "chat" and snap.next:
                return "chat"
    except Exception as exc:  # noqa: BLE001
        log.warning("paused-mode chat probe failed: %s", exc)
    return "briefing"


@app.get("/agent/pending/{thread_id}")
async def agent_pending(thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    async with compiled_graph(mode="briefing") as graph:
        snapshot = await graph.aget_state(config)
        pending = _extract_pending_interrupt(snapshot)
        if pending is None:
            raise HTTPException(status_code=404, detail="no pending interrupt")
        return {"thread_id": thread_id, "interrupt": pending}
