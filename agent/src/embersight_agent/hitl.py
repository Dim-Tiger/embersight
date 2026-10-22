"""Human-in-the-loop helpers.

Thin wrappers around LangGraph's `interrupt()` so every pause for human
review is recorded in the append-only audit log with a consistent envelope.

The actual pause/resume mechanism is LangGraph's: `interrupt(...)` raises
inside the node, the SqliteSaver persists the paused state, and the caller
resumes with `graph.invoke(Command(resume=...), config)`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.types import interrupt

from .state import InterruptRecord

InterruptType = Literal[
    "iap_approval",
    "resource_recommendation",
    "evac_zone_change",
    "trigger_point_violation",
]


def request_human_decision(
    interrupt_type: InterruptType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Pause the graph and wait for a human decision.

    The returned dict is whatever the caller passed to `Command(resume=...)`.
    Convention: `{"decision": "approved" | "edited" | "rejected", "edits": {...}, "actor": "user@..."}`.
    """
    envelope = {
        "id": str(uuid.uuid4()),
        "type": interrupt_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    decision = interrupt(envelope)
    return decision


DecisionKind = Literal["approved", "edited", "rejected", "unrecognized"]


def decision_kind(decision: Any) -> DecisionKind:
    """Normalise a resume value to one of the three canonical decisions.

    Anything else (a typo like "approve", an empty dict, a bare string)
    comes back as "unrecognized". Callers must treat that as NOT approved:
    a malformed resume should never publish a draft on the IC's behalf.
    """
    if not isinstance(decision, dict):
        return "unrecognized"
    kind = decision.get("decision")
    if kind in ("approved", "edited", "rejected"):
        return kind
    return "unrecognized"


def audit_entry(
    interrupt_type: InterruptType,
    payload: dict[str, Any],
    decision: dict[str, Any],
) -> InterruptRecord:
    """Build an InterruptRecord to append to AgentState.audit_log."""
    now = datetime.now(timezone.utc).isoformat()
    return InterruptRecord(
        interrupt_type=interrupt_type,
        payload=payload,
        decision=decision,
        created_at=now,
        resolved_at=now,
    )
