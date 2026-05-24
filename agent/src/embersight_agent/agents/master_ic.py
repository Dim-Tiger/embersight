"""Master IC synthesis agent.

Reads every upstream subagent output, runs a Claude Sonnet 4.5 synthesis
pass to draft the appropriate ICS form bundle for the current operational
period (ICS-201 for op period 1; ICS-202 + 203 + 204 + 215 + 215A for
later periods), builds a dissent log from low-confidence and
inter-agent-contradicting upstream claims, and ALWAYS pauses on the
`iap_approval` interrupt before the draft is considered actionable.

Hard repo rules enforced here (this agent is the canonical exemplar):
- No function/tool named ``dispatch_*`` / ``order_*`` / ``send_*`` /
  ``publish_*``. Master IC only DRAFTs; the human IC turns drafts into
  action.
- All user-facing verbs are ``RECOMMEND`` / ``PROPOSED`` / ``DRAFT`` /
  ``SUGGEST``. A post-synthesis regex sweep retries once if the model
  slips, then falls back to a deterministic draft.
- Harmonic-mean confidence over upstream subagents — a single low or
  missing input strongly drags the synthesis confidence toward zero.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..hitl import audit_entry, request_human_decision
from ..state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Dataset,
    Incident,
    Model,
)

AGENT_NAME = "master_ic"

# Default to Claude Sonnet 4.5 for synthesis. Override with the env var
# below if the deployment pins a different snapshot.
MODEL_ID = os.environ.get("EMBERSIGHT_MASTER_IC_MODEL", "claude-sonnet-4-5")

UPSTREAM_AGENTS: tuple[str, ...] = (
    "weather_wind",
    "terrain_fuel",
    "values_at_risk",
    "routing_staging",
    "spread_simulation",
    "resource_recommendation",
    "evacuation_intelligence",
)

# Words the synthesis is NEVER allowed to use in user-facing text. The
# Master IC drafts only; turning a draft into a real-world action is the
# human IC's job. The regex is grep-able so reviewers can confirm the
# constraint lives in code, not in prompt text alone.
FORBIDDEN_VERBS: tuple[str, ...] = ("dispatch", "order", "send", "publish")
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(FORBIDDEN_VERBS) + r")(?:ed|ing|s)?\b",
    re.IGNORECASE,
)

# Reference URLs used in the citation bundle. NWCG PMS 310-1 is the IRPG
# resource typing canon; FEMA hosts the canonical ICS form PDFs.
ICS_201_REFERENCE_URL = (
    "https://training.fema.gov/icsresource/icsforms.aspx"
)
NWCG_IAP_REFERENCE_URL = "https://www.nwcg.gov/publications/pms310-1"

log = logging.getLogger("embersight.master_ic")


# --------------------------------------------------------------------------- #
# Form selection
# --------------------------------------------------------------------------- #


def _form_type(op_period: int) -> str:
    """Op-period-1 gets ICS-201 (incident briefing). Later periods get the
    full IAP bundle (202 objectives, 203 org, 204 assignments, 215 planning
    worksheet, 215A safety analysis)."""
    return "ICS-201" if op_period <= 1 else "ICS-202-bundle"


# --------------------------------------------------------------------------- #
# Confidence aggregation
# --------------------------------------------------------------------------- #


def _harmonic_mean_confidence(state: AgentState) -> tuple[float, str]:
    """Harmonic mean of upstream subagent confidences.

    Returns ``(value, driver_string)``. Returns ``0.0`` if any upstream
    agent is missing or reports confidence == 0 — harmonic mean is the
    right aggregator here because a single weak input should drag the
    synthesis confidence toward zero rather than be averaged out.
    """
    values: list[float] = []
    missing: list[str] = []
    for name in UPSTREAM_AGENTS:
        out = state.outputs.get(name)
        if out is None:
            missing.append(name)
            continue
        values.append(float(out.confidence))

    if missing:
        return 0.0, f"missing upstream outputs: {', '.join(missing)}"
    if any(v <= 0.0 for v in values):
        zero_agents = [
            name
            for name, v in zip(UPSTREAM_AGENTS, values, strict=True)
            if v <= 0.0
        ]
        return 0.0, f"zero-confidence upstream: {', '.join(zero_agents)}"

    hmean = statistics.harmonic_mean(values)
    return round(hmean, 3), (
        "harmonic mean of upstream subagent confidences "
        f"(n={len(values)}, min={min(values):.2f}, max={max(values):.2f})"
    )


# --------------------------------------------------------------------------- #
# Dissent detection
# --------------------------------------------------------------------------- #


def _payload_text(out: AgentOutput | None) -> str:
    """Flatten an output's narrative + payload to lowercase searchable text."""
    if out is None:
        return ""
    try:
        body = json.dumps(out.payload, default=str)
    except Exception:  # noqa: BLE001
        body = str(out.payload)
    return (out.narrative + " " + body).lower()


def _dissent_entry(
    source_agent: str,
    conflicting_agent: str | None,
    claim_a: str,
    claim_b: str,
    severity: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "source_agent": source_agent,
        "conflicting_agent": conflicting_agent,
        "claim_a": claim_a,
        "claim_b": claim_b,
        "severity": severity,
        "rationale": rationale,
    }


def _detect_dissent(state: AgentState) -> list[dict[str, Any]]:
    """Build the dissent log from upstream outputs.

    Two classes of dissent are flagged:

    1. **Low-confidence** — any upstream subagent with confidence < 0.5.
       Synthesis can proceed but the draft must surface the uncertainty.
    2. **Inter-agent contradiction** — heuristic keyword checks across
       pairs of agents whose outputs disagree (e.g. high spread risk to a
       zone the values inventory says is empty; heavy-resource recommendation
       against light fuel; closed primary egress while evac plan still
       routes through it).
    """
    dissents: list[dict[str, Any]] = []
    outputs = state.outputs

    # 1) Missing-upstream dissent (synthesis still proceeds with stub gaps).
    for name in UPSTREAM_AGENTS:
        if name not in outputs:
            dissents.append(
                _dissent_entry(
                    source_agent=name,
                    conflicting_agent=None,
                    claim_a="(no output)",
                    claim_b="synthesis assumed neutral / placeholder values",
                    severity="high",
                    rationale=(
                        f"{name} did not return; downstream IAP sections "
                        "depending on it should be treated as preliminary."
                    ),
                )
            )

    # 2) Low-confidence dissent.
    for name in UPSTREAM_AGENTS:
        out = outputs.get(name)
        if out is None:
            continue
        if out.confidence < 0.5:
            dissents.append(
                _dissent_entry(
                    source_agent=name,
                    conflicting_agent=None,
                    claim_a=(
                        f"{name} reported confidence {out.confidence:.2f} — "
                        f"driver: {out.confidence_driver or 'unspecified'}"
                    ),
                    claim_b=(
                        "synthesis incorporated this input despite the low "
                        "confidence score"
                    ),
                    severity="medium" if out.confidence >= 0.3 else "high",
                    rationale=(
                        "Low-confidence inputs must be surfaced to the IC "
                        "so the approval decision is informed."
                    ),
                )
            )

    # 3) Spread-vs-Values contradiction.
    spread = outputs.get("spread_simulation")
    values = outputs.get("values_at_risk")
    if spread is not None and values is not None:
        spread_txt = _payload_text(spread)
        values_txt = _payload_text(values)
        spread_high = any(
            tok in spread_txt
            for tok in ("high risk", "high-risk", '"high"', "severe", "critical")
        )
        values_empty = any(
            tok in values_txt
            for tok in (
                "no structures",
                "zero structures",
                '"structures_count": 0',
                "structures_count: 0",
                "no values at risk",
                "no values-at-risk",
            )
        )
        if spread_high and values_empty:
            dissents.append(
                _dissent_entry(
                    source_agent="spread_simulation",
                    conflicting_agent="values_at_risk",
                    claim_a="spread simulation flags high-risk projected burn area",
                    claim_b="values-at-risk inventory reports no structures in cone",
                    severity="medium",
                    rationale=(
                        "Spread severity without exposed values implies a "
                        "pure-suppression posture; IC should confirm before "
                        "DRAFTing structure protection assignments."
                    ),
                )
            )

    # 4) Resource-vs-Terrain overkill / underkill.
    resource = outputs.get("resource_recommendation")
    terrain = outputs.get("terrain_fuel")
    if resource is not None and terrain is not None:
        r_txt = _payload_text(resource)
        t_txt = _payload_text(terrain)
        heavy = any(
            tok in r_txt
            for tok in ("type 1", "type-1", "type_1", "hotshot", "iht", "tanker")
        )
        light_fuel = any(
            tok in t_txt
            for tok in ("light grass", "gr1", "gr2", "grass fuel", "fbfm 1", "fuel light")
        )
        if heavy and light_fuel:
            dissents.append(
                _dissent_entry(
                    source_agent="resource_recommendation",
                    conflicting_agent="terrain_fuel",
                    claim_a="resource rec proposes Type-1 / hotshot assets",
                    claim_b="terrain/fuel characterization indicates light grass fuel",
                    severity="medium",
                    rationale=(
                        "Type-1 assets against GR1/GR2 fuel may be overkill; "
                        "IC should confirm fuel model before approving."
                    ),
                )
            )

    # 5) Routing-vs-Evacuation contradiction.
    routing = outputs.get("routing_staging")
    evac = outputs.get("evacuation_intelligence")
    if routing is not None and evac is not None:
        ro_txt = _payload_text(routing)
        ev_txt = _payload_text(evac)
        if "closed" in ro_txt and ("primary egress" in ev_txt or "primary route" in ev_txt):
            dissents.append(
                _dissent_entry(
                    source_agent="routing_staging",
                    conflicting_agent="evacuation_intelligence",
                    claim_a="routing reports closures on candidate roads",
                    claim_b="evacuation plan still references primary egress routes",
                    severity="high",
                    rationale=(
                        "A closed primary egress invalidates the evac route; "
                        "IC must reconcile before approving the IAP."
                    ),
                )
            )

    return dissents


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def _load_system_prompt() -> str:
    p = Path(__file__).resolve().parent.parent / "prompts" / "master_ic.md"
    base = p.read_text() if p.exists() else ""
    # Addendum repeats the verb constraint with explicit instructions on how
    # to rephrase common action verbs. Reviewers: the forbidden words appear
    # here only inside a 'NEVER use' list — this is the documented escape.
    addendum = (
        "\n\n## HARD CONSTRAINT (enforced by post-synthesis regex)\n"
        "You must NOT use any of these words anywhere in your output, "
        "neither as a verb nor a noun: dispatch, order, send, publish "
        "(or their conjugations: dispatched, dispatching, ordered, sending, "
        "published, etc.).\n\n"
        "Use these instead:\n"
        "- Instead of 'dispatch X' write 'RECOMMEND mobilization of X'\n"
        "- Instead of 'order N units' write 'PROPOSED N units' or 'DRAFT request for N units'\n"
        "- Instead of 'send notification' write 'DRAFT notification' or 'SUGGEST notification'\n"
        "- Instead of 'publish IAP' write 'DRAFT IAP for IC approval'\n\n"
        "## Output format\n"
        "Respond ONLY with a single fenced JSON block (```json ... ```). The "
        "JSON object MUST contain these top-level keys:\n"
        "- form: \"ICS-201\" or \"ICS-202-bundle\"\n"
        "- operational_period: int\n"
        "- objectives: list[str] (first entry MUST be a life-safety objective)\n"
        "- key_findings: list[str] (top 5 most important per-section highlights)\n"
        "- sections: object (form-specific; see below)\n"
        "- dissent_log_acknowledgment: list[str] (one short sentence per dissent entry from the upstream dissent log)\n\n"
        "For ICS-201, `sections` must include: incident_name, prepared_by, "
        "map_sketch_placeholder, current_situation, planned_actions, "
        "current_org_chart (with incident_commander, deputies, ops_section_chief, "
        "planning_section_chief, logistics_section_chief, finance_section_chief), "
        "resources_summary, immediate_concerns.\n\n"
        "For ICS-202-bundle, `sections` must include `ics_202`, `ics_203`, "
        "`ics_204` (list, one per Division/Group), `ics_215`, `ics_215a`. "
        "Each is a structured object.\n"
    )
    return base + addendum


def _serialize_upstream(state: AgentState) -> dict[str, Any]:
    """Trim each upstream output to the fields the LLM actually needs."""
    serialized: dict[str, Any] = {}
    for name in UPSTREAM_AGENTS:
        out = state.outputs.get(name)
        if out is None:
            serialized[name] = {"_missing": True}
            continue
        serialized[name] = {
            "narrative": out.narrative,
            "confidence": out.confidence,
            "confidence_driver": out.confidence_driver,
            "payload": out.payload,
        }
    return serialized


def _build_user_message(
    state: AgentState,
    form_type: str,
    dissent_log: list[dict[str, Any]],
) -> str:
    incident = state.incident.model_dump() if state.incident else None
    body = {
        "task": (
            f"Draft an {form_type} for operational period "
            f"{state.operational_period}. Output a single JSON object as "
            "specified in the system prompt."
        ),
        "incident": incident,
        "operational_period": state.operational_period,
        "user_query": state.user_query,
        "upstream_outputs": _serialize_upstream(state),
        "dissent_log": dissent_log,
    }
    return json.dumps(body, indent=2, default=str)


# --------------------------------------------------------------------------- #
# LLM synthesis with verb-constraint retry
# --------------------------------------------------------------------------- #


def _extract_json_block(text: str) -> dict[str, Any]:
    """Pull the first ```json ... ``` block, or fall back to the first
    well-formed top-level object."""
    fence = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace:
        return json.loads(brace.group(1))
    raise ValueError("no JSON object found in LLM output")


def _forbidden_hits(text: str) -> list[str]:
    return [m.group(0) for m in _FORBIDDEN_RE.finditer(text)]


async def _llm_synthesize(
    state: AgentState,
    form_type: str,
    dissent_log: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Run the Sonnet 4.5 synthesis pass with one verb-constraint retry.

    Returns the parsed draft dict on success, or ``None`` if the API key is
    missing or any of (network call, JSON parse, retry) fails — the caller
    is responsible for falling back to the deterministic synthesis.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY not set; skipping LLM synthesis")
        return None

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    except ImportError:
        log.warning("langchain_anthropic unavailable; using deterministic fallback")
        return None

    system_prompt = _load_system_prompt()
    user_msg = _build_user_message(state, form_type, dissent_log)

    try:
        llm = ChatAnthropic(model=MODEL_ID, temperature=0.2, max_tokens=4096)
    except Exception as exc:  # noqa: BLE001
        log.warning("ChatAnthropic init failed: %s", exc)
        return None

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]

    from ..tools.llm_stream import stream_text

    for attempt in (1, 2):
        try:
            raw = await stream_text(llm, messages)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM call failed on attempt %d: %s", attempt, exc)
            return None

        resp = AIMessage(content=raw)
        hits = _forbidden_hits(raw)
        if not hits:
            try:
                return _extract_json_block(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                log.warning("JSON parse failed on attempt %d: %s", attempt, exc)
                if attempt == 1:
                    messages.append(resp)
                    messages.append(
                        HumanMessage(
                            content=(
                                "Your previous response was not valid JSON. "
                                "Re-emit a single fenced ```json ... ``` block "
                                "exactly matching the schema in the system prompt."
                            )
                        )
                    )
                    continue
                return None

        # Forbidden verb hit — ask for a rewrite, then give up.
        log.warning("forbidden verb hits on attempt %d: %s", attempt, hits)
        if attempt == 1:
            messages.append(resp)
            messages.append(
                HumanMessage(
                    content=(
                        "Your previous response contained these forbidden "
                        f"words: {sorted(set(h.lower() for h in hits))}. "
                        "Rewrite the draft using only RECOMMEND / PROPOSED / "
                        "DRAFT / SUGGEST. Re-emit the full JSON block."
                    )
                )
            )
            continue
        return None

    return None


# --------------------------------------------------------------------------- #
# Deterministic fallback synthesis
# --------------------------------------------------------------------------- #


def _deterministic_ics_201(
    state: AgentState, dissent_log: list[dict[str, Any]]
) -> dict[str, Any]:
    incident = state.incident
    inc_name = incident.name if incident else "(unspecified incident)"
    return {
        "form": "ICS-201",
        "operational_period": state.operational_period,
        "objectives": [
            "Provide for responder and public safety (life-safety, always #1).",
            "RECOMMEND containment of the fire perimeter at defensible terrain features.",
            "PROPOSED protection of identified values-at-risk in the projected spread cone.",
            "DRAFT communications cadence with cooperating agencies every 2 hours.",
        ],
        "key_findings": _deterministic_key_findings(state),
        "sections": {
            "incident_name": inc_name,
            "prepared_by": "EmberSight Master IC synthesis (DRAFT, pending IC approval)",
            "map_sketch_placeholder": (
                "Map sketch RECOMMENDED for IC review — geo overlay rendered "
                "in the dashboard map pane."
            ),
            "current_situation": (
                f"Incident {inc_name} active. Synthesis incorporated "
                f"{len(state.outputs)} upstream subagent outputs."
            ),
            "planned_actions": [
                "RECOMMEND initial attack posture: anchor and flank from defensible terrain.",
                "PROPOSED staging area established at the routing agent's top-ranked candidate.",
                "DRAFT evacuation advisory for zones identified by evacuation_intelligence.",
            ],
            "current_org_chart": {
                "incident_commander": "(pending IC assignment)",
                "deputies": [],
                "ops_section_chief": "(PROPOSED)",
                "planning_section_chief": "(PROPOSED)",
                "logistics_section_chief": "(PROPOSED)",
                "finance_section_chief": "(PROPOSED)",
            },
            "resources_summary": (
                "See resource_recommendation subagent output for proposed "
                "apparatus / crews / aircraft."
            ),
            "immediate_concerns": [
                d["claim_a"] for d in dissent_log[:5]
            ] or ["No high-severity dissent flagged."],
        },
        "dissent_log_acknowledgment": [
            f"{d['source_agent']}: {d['rationale']}" for d in dissent_log
        ],
    }


def _deterministic_ics_202_bundle(
    state: AgentState, dissent_log: list[dict[str, Any]]
) -> dict[str, Any]:
    incident = state.incident
    inc_name = incident.name if incident else "(unspecified incident)"
    objectives = [
        "Provide for responder and public safety (life-safety, always #1).",
        "RECOMMEND containment of the fire perimeter at defensible terrain features.",
        "PROPOSED protection of values-at-risk in the projected spread cone.",
        "DRAFT continued reassessment of evacuation zones at start of each op period.",
    ]
    return {
        "form": "ICS-202-bundle",
        "operational_period": state.operational_period,
        "objectives": objectives,
        "key_findings": _deterministic_key_findings(state),
        "sections": {
            "ics_202": {
                "incident_name": inc_name,
                "operational_period": state.operational_period,
                "objectives": objectives,
                "weather_summary": (
                    "See weather_wind output for HRRR/RTMA-fused forecast."
                ),
                "safety_message": (
                    "Heads-up on dissent log entries flagged by Master IC; "
                    "DRAFT — pending IC approval."
                ),
                "prepared_by": "EmberSight Master IC synthesis (DRAFT)",
            },
            "ics_203": {
                "incident_commander": "(pending IC assignment)",
                "command_staff": {
                    "safety_officer": "(PROPOSED)",
                    "public_information_officer": "(PROPOSED)",
                    "liaison_officer": "(PROPOSED)",
                },
                "general_staff": {
                    "ops_section_chief": "(PROPOSED)",
                    "planning_section_chief": "(PROPOSED)",
                    "logistics_section_chief": "(PROPOSED)",
                    "finance_section_chief": "(PROPOSED)",
                },
            },
            "ics_204": [
                {
                    "branch_division_group": "Division A",
                    "operational_period": state.operational_period,
                    "work_assignments": [
                        "RECOMMEND anchor + flank from anchor point identified by routing_staging.",
                        "PROPOSED structure triage along the values_at_risk inventory.",
                    ],
                    "resources_assigned": [
                        "(see resource_recommendation output for PROPOSED resources)"
                    ],
                    "special_instructions": (
                        "DRAFT only — pending IC approval. Re-confirm radio "
                        "freqs at op-briefing."
                    ),
                }
            ],
            "ics_215": {
                "operational_period": state.operational_period,
                "work_assignments_by_division": [
                    {
                        "division": "A",
                        "work_assignment_summary": "Anchor + flank, structure triage",
                        "resources_required": "Per resource_recommendation PROPOSED list",
                    }
                ],
                "notes": "DRAFT planning worksheet — pending IC and OPS sign-off.",
            },
            "ics_215a": {
                "hazards": [
                    d["claim_a"] for d in dissent_log if d["severity"] in ("medium", "high")
                ] or ["No high-severity hazard surfaced by upstream dissent."],
                "mitigations": [
                    "RECOMMEND LCES (Lookouts, Communications, Escape routes, Safety zones) refresh at op-briefing.",
                    "PROPOSED safety-officer review of every Division/Group assignment before execution.",
                ],
            },
        },
        "dissent_log_acknowledgment": [
            f"{d['source_agent']}: {d['rationale']}" for d in dissent_log
        ],
    }


def _deterministic_key_findings(state: AgentState) -> list[str]:
    findings: list[str] = []
    for name in UPSTREAM_AGENTS:
        out = state.outputs.get(name)
        if out is None:
            findings.append(f"{name}: (no output — synthesis used neutral default)")
            continue
        snippet = (out.narrative or "").strip().replace("\n", " ")[:140]
        findings.append(f"{name} (conf={out.confidence:.2f}): {snippet}")
        if len(findings) >= 5:
            break
    return findings[:5]


def _deterministic_synthesis(
    state: AgentState, form_type: str, dissent_log: list[dict[str, Any]]
) -> dict[str, Any]:
    if form_type == "ICS-201":
        return _deterministic_ics_201(state, dissent_log)
    return _deterministic_ics_202_bundle(state, dissent_log)


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #


def _build_citation_bundle(used_llm: bool) -> CitationBundle:
    datasets = [
        Dataset(
            name="ICS Forms (FEMA)",
            version="ICS-201/202/203/204/215/215A",
            url=ICS_201_REFERENCE_URL,
        ),
        Dataset(
            name="NWCG IRPG / PMS 310-1",
            version="current",
            url=NWCG_IAP_REFERENCE_URL,
        ),
    ]
    models = [
        Model(
            name=MODEL_ID if used_llm else "deterministic-fallback",
            version="synthesis-v1",
        )
    ]
    return CitationBundle(
        datasets=datasets,
        models=models,
        reasoning_trace_id=str(uuid.uuid4()),
    )


async def run(state: AgentState) -> dict:
    op_period = max(1, int(state.operational_period or 1))
    form_type = _form_type(op_period)

    dissent_log = _detect_dissent(state)
    confidence, conf_driver = _harmonic_mean_confidence(state)

    used_llm = False
    draft = await _llm_synthesize(state, form_type, dissent_log)
    if draft is None:
        draft = _deterministic_synthesis(state, form_type, dissent_log)
    else:
        used_llm = True
        # Defensive: even if the LLM passed the regex check, make sure the
        # form/op_period fields are correct in case the model drifted.
        draft.setdefault("form", form_type)
        draft.setdefault("operational_period", op_period)

    # Final sanity sweep on serialized draft. If a forbidden verb still slips
    # through (e.g. inside a payload value the LLM added), fall back rather
    # than ship a non-compliant artifact.
    serialized_draft = json.dumps(draft, default=str)
    if _forbidden_hits(serialized_draft):
        log.warning(
            "forbidden verb survived LLM draft; falling back to deterministic synthesis"
        )
        draft = _deterministic_synthesis(state, form_type, dissent_log)
        used_llm = False

    citation_bundle = _build_citation_bundle(used_llm)

    interrupt_envelope = {
        "type": "iap_approval",
        "form_type": form_type,
        "draft": draft,
        "dissent_log": dissent_log,
        "confidence": confidence,
        "citations": citation_bundle.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    decision = request_human_decision("iap_approval", interrupt_envelope)

    # Decision branching.
    decision_kind = (decision or {}).get("decision", "approved")
    edited_draft = (decision or {}).get("edited_draft")
    reject_reason = (decision or {}).get("reason")

    final_draft: dict[str, Any]
    if decision_kind == "approved":
        final_draft = draft
    elif decision_kind == "edited" and isinstance(edited_draft, dict):
        final_draft = edited_draft
    elif decision_kind == "rejected":
        final_draft = {}
        dissent_log.append(
            _dissent_entry(
                source_agent="human_ic",
                conflicting_agent=AGENT_NAME,
                claim_a="Master IC DRAFT rejected by human IC",
                claim_b=reject_reason or "(no reason provided)",
                severity="high",
                rationale=(
                    "IC rejected the synthesis; downstream sections marked "
                    "invalid until re-DRAFT."
                ),
            )
        )
    else:
        # Unknown decision keyword — treat as approved-with-warning so the
        # graph still terminates, but record the unexpected envelope.
        final_draft = draft
        dissent_log.append(
            _dissent_entry(
                source_agent="human_ic",
                conflicting_agent=AGENT_NAME,
                claim_a=f"Unrecognized decision: {decision_kind!r}",
                claim_b="treated as approved",
                severity="medium",
                rationale="Unexpected interrupt envelope; review audit log.",
            )
        )

    narrative = (
        f"Master IC DRAFT {form_type} for operational period {op_period}: "
        f"decision={decision_kind}, confidence={confidence:.2f}, "
        f"dissents={len(dissent_log)}, synthesis="
        f"{'LLM' if used_llm else 'deterministic'}."
    )

    payload = {
        "iap_draft": final_draft,
        "form_type": form_type,
        "decision": decision_kind,
        "dissent_log": dissent_log,
        "key_findings": draft.get("key_findings") or _deterministic_key_findings(state),
        "synthesis_source": "llm" if used_llm else "deterministic",
    }

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload=payload,
        confidence=confidence,
        confidence_driver=conf_driver,
        citation_bundle=citation_bundle,
    )

    audit_record = audit_entry("iap_approval", interrupt_envelope, decision or {})

    patch: dict[str, Any] = {
        "outputs": {AGENT_NAME: output},
        "audit_log": [audit_record],
        "dissent_log": dissent_log,
    }
    # Mirror the approved/edited draft onto the state-level slot so the
    # dashboard can render it without digging into output.payload.
    if final_draft:
        patch["iap_draft"] = final_draft

    return patch


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _mock_state_with_dissents() -> AgentState:
    """Build a realistic mock state that triggers >= 2 dissent entries."""
    incident = Incident(
        id="CA-LNU-000123",
        name="Hawthorne",
        lat=38.5,
        lon=-122.7,
        acres=450.0,
        contained_pct=10.0,
        started_at="2026-05-23T14:22:00Z",
        source="calfire",
    )

    def _out(
        agent: str,
        narrative: str,
        confidence: float,
        payload: dict[str, Any],
        driver: str = "stub",
    ) -> AgentOutput:
        return AgentOutput(
            agent=agent,
            narrative=narrative,
            payload=payload,
            confidence=confidence,
            confidence_driver=driver,
            citation_bundle=CitationBundle(
                datasets=[Dataset(name="(stub)", version="0")],
                models=[Model(name="(stub)", version="0")],
            ),
        )

    outputs = {
        "weather_wind": _out(
            "weather_wind",
            "HRRR + RTMA fused: SW wind 18 gust 28 mph, RH 14%, Red Flag in effect.",
            0.78,
            {"wind_dir_deg": 225, "wind_mph": 18, "gust_mph": 28, "rh_pct": 14},
            "HRRR/RTMA wind agreement",
        ),
        "terrain_fuel": _out(
            "terrain_fuel",
            "LANDFIRE FBFM40 dominant class GR1 light grass; slope 18 deg NE aspect.",
            0.7,
            {"fuel_model": "GR1 light grass", "slope_deg": 18, "aspect": "NE"},
            "fuel-model purity",
        ),
        "values_at_risk": _out(
            "values_at_risk",
            "No structures within 12h projected cone; closest school 4.2 mi SE.",
            0.8,
            {"structures_count": 0, "nearest_school_mi": 4.2, "no_structures": True},
            "MS Building Footprints recent",
        ),
        "routing_staging": _out(
            "routing_staging",
            "Top staging candidate: Route 128 paved turnout. Note Hwy 29 closed.",
            0.4,
            {
                "candidates": [{"name": "Rt128 turnout", "score": 0.81}],
                "closures": ["Hwy 29 closed at MP 14"],
            },
            "OSM coverage sparse",
        ),
        "spread_simulation": _out(
            "spread_simulation",
            "Pyretechnics ensemble: HIGH risk to zone N4 within 6h; head ROS 9 ch/hr.",
            0.55,
            {
                "head_ros_chains_per_hr": 9,
                "risk_level": "high",
                "high_risk_zones": ["N4"],
            },
            "ensemble spread",
        ),
        "resource_recommendation": _out(
            "resource_recommendation",
            "PROPOSED 1x Type-1 hotshot crew + 2x Type-3 engines + 1 air tanker.",
            0.65,
            {
                "proposed": [
                    {"kind": "Type-1 hotshot crew", "qty": 1},
                    {"kind": "Type-3 engine", "qty": 2},
                    {"kind": "air tanker", "qty": 1},
                ]
            },
            "NWCG resource typing",
        ),
        "evacuation_intelligence": _out(
            "evacuation_intelligence",
            "PROPOSED evacuation Zone 4 advisory; primary egress via Hwy 29.",
            0.6,
            {
                "zones": ["Zone 4"],
                "primary egress": "Hwy 29",
                "advisory_level": "advisory",
            },
        ),
    }

    return AgentState(
        incident=incident,
        operational_period=1,
        user_query="(smoke test)",
        outputs=outputs,
    )


async def _smoke() -> None:
    import embersight_agent.agents.master_ic as me  # type: ignore

    decisions_to_test = [
        {"decision": "approved", "edited_draft": None, "reason": None},
        {
            "decision": "edited",
            "edited_draft": {
                "form": "ICS-201",
                "operational_period": 1,
                "objectives": ["LIFE SAFETY first (edited by IC)"],
                "sections": {"incident_name": "Hawthorne (edited)"},
            },
            "reason": None,
        },
        {
            "decision": "rejected",
            "edited_draft": None,
            "reason": "Wrong fuel model assumption",
        },
    ]

    original_hitl = me.request_human_decision

    for stub_decision in decisions_to_test:
        # Monkey-patch the imported request_human_decision so the smoke test
        # runs end-to-end without LangGraph orchestration.
        def _fake(_kind, _payload, _d=stub_decision):  # noqa: ANN001
            return _d

        me.request_human_decision = _fake  # type: ignore[assignment]
        try:
            state = _mock_state_with_dissents()
            patch = await me.run(state)
        finally:
            me.request_human_decision = original_hitl  # type: ignore[assignment]

        out = patch["outputs"][AGENT_NAME]
        assert 0.0 <= out.confidence <= 1.0, "confidence out of range"
        assert isinstance(out.payload["iap_draft"], dict), "iap_draft not dict"
        dissents = patch["dissent_log"]
        assert len(dissents) >= 2, f"expected >= 2 dissent entries, got {len(dissents)}"
        assert len(patch["audit_log"]) == 1, "missing audit record"

        kind = stub_decision["decision"]
        print(f"--- smoke: decision={kind} ---")
        print(f"  form_type             : {out.payload['form_type']}")
        print(f"  decision              : {out.payload['decision']}")
        print(f"  synthesis_source      : {out.payload['synthesis_source']}")
        print(f"  confidence            : {out.confidence}")
        print(f"  confidence_driver     : {out.confidence_driver}")
        print(f"  dissent_log_entries   : {len(dissents)}")
        print(f"  iap_draft_keys        : {sorted((out.payload['iap_draft'] or {}).keys())}")
        print(f"  narrative             : {out.narrative}")
        print()

    print("smoke test passed.")


if __name__ == "__main__":
    asyncio.run(_smoke())
