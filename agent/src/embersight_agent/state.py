"""Pydantic state shared across the LangGraph agent graph.

Every subagent emits a structured output that lands on AgentState. Every
field that represents an analytical product carries a CitationBundle and a
confidence score, so the UI can render provenance and uncertainty next to
every recommendation.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


class Dataset(BaseModel):
    name: str
    version: str | None = None
    timestamp: str | None = None
    url: str | None = None


class Model(BaseModel):
    name: str
    version: str | None = None


class CitationBundle(BaseModel):
    datasets: list[Dataset] = Field(default_factory=list)
    models: list[Model] = Field(default_factory=list)
    reasoning_trace_id: str | None = None


class AgentOutput(BaseModel):
    """Wrapper every subagent must return.

    `narrative` is the human-readable summary the UI shows in the agent feed.
    `payload` is the structured artifact (typed per-agent in real impl).
    """

    agent: str
    narrative: str
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    confidence_driver: str = ""
    citation_bundle: CitationBundle = Field(default_factory=CitationBundle)


# --------------------------------------------------------------------------- #
# Domain artifacts (placeholders to be fleshed out in pass 2)
# --------------------------------------------------------------------------- #


class Incident(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    acres: float | None = None
    contained_pct: float | None = None
    started_at: str | None = None
    source: Literal["calfire", "wfigs", "synthetic"] = "calfire"
    raw: dict[str, Any] = Field(default_factory=dict)


class InterruptRecord(BaseModel):
    """One row of the append-only audit log."""

    interrupt_type: str
    payload: dict[str, Any]
    decision: dict[str, Any] | None = None
    created_at: str
    resolved_at: str | None = None


# --------------------------------------------------------------------------- #
# Top-level state
# --------------------------------------------------------------------------- #


def _merge_outputs(
    left: dict[str, AgentOutput], right: dict[str, AgentOutput]
) -> dict[str, AgentOutput]:
    """Reducer so parallel subagents can write into `outputs` without clobbering."""
    return {**left, **right}


class AgentState(BaseModel):
    """LangGraph state object.

    Annotated reducers let parallel `Send`-fanned subagents merge results
    without stomping on each other.
    """

    # Inputs
    incident: Incident | None = None
    operational_period: int = 1
    user_query: str = ""

    # Per-subagent outputs, keyed by agent name
    outputs: Annotated[dict[str, AgentOutput], _merge_outputs] = Field(
        default_factory=dict
    )

    # Master IC synthesis
    iap_draft: dict[str, Any] | None = None
    dissent_log: Annotated[list[dict[str, Any]], operator.add] = Field(
        default_factory=list
    )

    # HITL audit trail
    audit_log: Annotated[list[InterruptRecord], operator.add] = Field(
        default_factory=list
    )

    # Free-form scratchpad for orchestrator
    scratch: dict[str, Any] = Field(default_factory=dict)
