"""Shared stub helper used by pass-1 placeholder subagents."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model


def make_stub_output(
    *,
    agent: str,
    role: str,
    state: AgentState,
    confidence: float = 0.5,
    confidence_driver: str = "stub implementation",
    extra_payload: dict[str, Any] | None = None,
) -> AgentOutput:
    incident_name = state.incident.name if state.incident else "no-incident"
    return AgentOutput(
        agent=agent,
        narrative=(
            f"[{agent}] {role}. Incident: {incident_name}. "
            "Pass-1 stub — real tool calls land in pass 2."
        ),
        payload={"role": role, **(extra_payload or {})},
        confidence=confidence,
        confidence_driver=confidence_driver,
        citation_bundle=CitationBundle(
            datasets=[Dataset(name="(stub)", version="0", timestamp="", url="")],
            models=[Model(name="(stub)", version="0")],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )


async def stub_run(
    agent: str, role: str, state: AgentState, *, latency_s: float = 0.2
) -> dict[str, Any]:
    """Sleep a tiny bit (so the stream feels alive) and return a patch."""
    await asyncio.sleep(latency_s)
    return {"outputs": {agent: make_stub_output(agent=agent, role=role, state=state)}}
