"""Master IC synthesis agent.

Reads every subagent output, drafts the appropriate ICS form bundle for the
current operational period (ICS 201 for op period 1; ICS 202/204/215/215A
for later periods), and ALWAYS pauses on an `iap_approval` interrupt
before the draft is considered actionable.

Pass-1 returns a deterministic stub IAP draft so the HITL loop is testable
without an LLM round-trip.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..hitl import audit_entry, request_human_decision
from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model

AGENT_NAME = "master_ic"


def _draft_iap(state: AgentState) -> dict:
    op = state.operational_period
    form = "ICS-201" if op == 1 else "ICS-202+204+215+215A"
    incident = state.incident
    return {
        "form": form,
        "operational_period": op,
        "incident": incident.model_dump() if incident else None,
        "objectives": [
            "Provide for responder and public safety (always #1).",
            "Contain the fire perimeter at defensible terrain features.",
            "Protect identified values-at-risk in the projected spread cone.",
        ],
        "subagent_outputs": list(state.outputs.keys()),
        "dissent_log": state.dissent_log,
        "drafted_at": datetime.now(timezone.utc).isoformat(),
        "status": "DRAFT - pending IC approval",
        "verbs_disclaimer": "RECOMMEND / PROPOSE / DRAFT only.",
    }


async def run(state: AgentState) -> dict:
    draft = _draft_iap(state)
    confidence = 0.6

    summary = AgentOutput(
        agent=AGENT_NAME,
        narrative=(
            "Master IC has synthesized subagent outputs into a draft "
            f"{draft['form']} for operational period {draft['operational_period']}. "
            "Pausing for human IC approval."
        ),
        payload={"iap_draft": draft},
        confidence=confidence,
        confidence_driver="weighted average of subagent confidences (stub)",
        citation_bundle=CitationBundle(
            datasets=[Dataset(name="(synthesis)", version="0")],
            models=[Model(name="(synthesis)", version="0")],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )

    interrupt_payload = {
        "draft": draft,
        "confidence": confidence,
        "citations": summary.citation_bundle.model_dump(),
    }
    decision = request_human_decision("iap_approval", interrupt_payload)

    return {
        "outputs": {AGENT_NAME: summary},
        "iap_draft": {**draft, "decision": decision},
        "audit_log": [audit_entry("iap_approval", interrupt_payload, decision)],
    }
