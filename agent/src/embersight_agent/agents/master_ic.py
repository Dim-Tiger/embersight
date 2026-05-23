"""Master IC synthesis agent.

Reads every subagent output, drafts the appropriate ICS form bundle for the
current operational period (ICS-201 for op period 1; ICS-202 / 204 / 215 /
215A bundle for later periods), and ALWAYS pauses on an ``iap_approval``
interrupt before the draft is considered actionable.

Verb constraint -- repeated here so future readers cannot miss it: this
module never produces the action verbs ``dispatch``, ``order``, ``send``,
or ``publish``. Every imperative is phrased as ``RECOMMEND`` / ``PROPOSE``
/ ``DRAFT`` / ``SUGGEST``. A human Incident Commander is the only actor
that can act on these draft artifacts. Build-time grep over this file
should surface those four verbs only inside this constraint docstring.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..hitl import audit_entry, request_human_decision
from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model

AGENT_NAME = "master_ic"

_DEFAULT_MODEL = "claude-sonnet-4-5"
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "master_ic.md"

_UPSTREAM_AGENTS: tuple[str, ...] = (
    "weather_wind",
    "terrain_fuel",
    "values_at_risk",
    "routing_staging",
    "spread_simulation",
    "resource_recommendation",
    "evacuation_intelligence",
)

_ICS_SCHEMA_REF = Dataset(
    name="FEMA NIMS ICS Forms (2010 rev.)",
    version="2010",
    url="https://www.fema.gov/emergency-managers/nims/components#ics-forms",
)


# --------------------------------------------------------------------------- #
# Pydantic schema for structured LLM output
# --------------------------------------------------------------------------- #


class IAPSection(BaseModel):
    """One labelled section of the draft (e.g. 'Situation', 'Objectives')."""

    title: str
    body: str


class Assignment(BaseModel):
    """One Division/Group line on ICS-204 (op-period 2+)."""

    division: str
    resources: list[str] = Field(default_factory=list)
    work_assignment: str
    special_instructions: str = ""


class IAPDraftModel(BaseModel):
    """Structured output the synthesis LLM must produce.

    Note: every imperative in ``objectives`` / ``assignments`` /
    ``sections.body`` must read ``RECOMMEND`` / ``PROPOSE`` / ``DRAFT`` /
    ``SUGGEST`` per the system prompt's verb constraint.
    """

    form: str
    operational_period: int
    objectives: list[str]
    sections: list[IAPSection]
    assignments: list[Assignment] = Field(default_factory=list)
    key_findings: list[str]
    safety_message: str


# --------------------------------------------------------------------------- #
# Dissent detection
# --------------------------------------------------------------------------- #


def _low_confidence_entries(state: AgentState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, ao in state.outputs.items():
        if ao.confidence < 0.5:
            out.append(
                {
                    "agent": name,
                    "kind": "low_confidence",
                    "confidence": ao.confidence,
                    "concern": (
                        f"{name} reported confidence {ao.confidence:.2f} < 0.5 "
                        f"({ao.confidence_driver or 'no driver given'})."
                    ),
                    "rationale": (
                        "Surfaced for IC review; synthesis proceeded but flagged."
                    ),
                }
            )
    return out


def _conflict_entries(state: AgentState) -> list[dict[str, Any]]:
    """Detect known cross-agent conflict patterns.

    Wired pair: spread_simulation flagging high-risk zones while
    values_at_risk reports no structures sitting in those zones. Add more
    patterns as upstream payload shapes stabilize.
    """
    entries: list[dict[str, Any]] = []
    spread = state.outputs.get("spread_simulation")
    var = state.outputs.get("values_at_risk")
    if spread is not None and var is not None:
        spread_high = bool(
            spread.payload.get("high_risk_zones")
            or spread.payload.get("trigger_breached")
        )
        structures = var.payload.get("structures_at_risk")
        in_cone = var.payload.get("structures_in_cone")
        var_empty = structures == [] or in_cone == 0
        if spread_high and var_empty:
            entries.append(
                {
                    "agents": ["spread_simulation", "values_at_risk"],
                    "kind": "conflict",
                    "concern": (
                        "spread_simulation flagged high-risk zones "
                        f"({spread.payload.get('high_risk_zones')}) but "
                        "values_at_risk reports no structures in those zones."
                    ),
                    "rationale": (
                        "Possible spatial-extent disagreement between models; "
                        "IC must reconcile before approving assignments."
                    ),
                }
            )
    return entries


def _build_dissent_log(state: AgentState) -> list[dict[str, Any]]:
    return [*_low_confidence_entries(state), *_conflict_entries(state)]


# --------------------------------------------------------------------------- #
# Confidence aggregation
# --------------------------------------------------------------------------- #


def _harmonic_mean(values: list[float]) -> float:
    """Harmonic mean -- a single low input drags the aggregate down hard."""
    clean = [v for v in values if v > 0]
    if not clean:
        return 0.0
    return len(clean) / sum(1.0 / v for v in clean)


def _aggregate_confidence(state: AgentState) -> tuple[float, str]:
    upstream = [
        state.outputs[a].confidence
        for a in _UPSTREAM_AGENTS
        if a in state.outputs
    ]
    if not upstream:
        return 0.0, "no upstream outputs available"
    hm = _harmonic_mean(upstream)
    driver = (
        f"harmonic mean of {len(upstream)} upstream confidences "
        f"(min={min(upstream):.2f}, max={max(upstream):.2f})"
    )
    return round(hm, 3), driver


# --------------------------------------------------------------------------- #
# Form selection
# --------------------------------------------------------------------------- #


def _form_for(op_period: int) -> str:
    return "ICS-201" if op_period <= 1 else "ICS-202-bundle"


# --------------------------------------------------------------------------- #
# Upstream packaging for the LLM
# --------------------------------------------------------------------------- #


def _condense_upstream(state: AgentState) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for name in _UPSTREAM_AGENTS:
        ao = state.outputs.get(name)
        if ao is None:
            bundle[name] = {"missing": True}
            continue
        bundle[name] = {
            "narrative": ao.narrative,
            "payload": ao.payload,
            "confidence": ao.confidence,
            "confidence_driver": ao.confidence_driver,
            "reasoning_trace_id": ao.citation_bundle.reasoning_trace_id,
        }
    return bundle


# --------------------------------------------------------------------------- #
# LLM synthesis
# --------------------------------------------------------------------------- #


def _read_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text()
    except OSError:
        return "You are the Master IC synthesis agent. Draft an ICS form."


async def _llm_synthesize(
    state: AgentState,
    form: str,
    upstream: dict[str, Any],
    dissent_log: list[dict[str, Any]],
) -> IAPDraftModel | None:
    """Synthesize via claude-sonnet-4-5 using structured output.

    Returns ``None`` (caller falls back to deterministic draft) when:
      - ANTHROPIC_API_KEY is unset
      - the langchain_anthropic import fails
      - the LLM round-trip errors
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        return None

    model_id = os.environ.get("EMBERSIGHT_MODEL_MASTER_IC", _DEFAULT_MODEL)
    if ":" in model_id:
        model_id = model_id.split(":", 1)[1]

    try:
        llm = ChatAnthropic(model=model_id, max_tokens=4096, temperature=0.2)
        structured = llm.with_structured_output(IAPDraftModel)
    except Exception:  # noqa: BLE001 -- any client init failure -> fallback
        return None

    user_msg = json.dumps(
        {
            "instruction": (
                f"Draft a {form} for operational period "
                f"{state.operational_period}. Follow the verb constraint "
                "stated in your system prompt strictly. First objective "
                "MUST be a life-safety objective."
            ),
            "incident": state.incident.model_dump() if state.incident else None,
            "upstream_outputs": upstream,
            "dissent_log": dissent_log,
            "user_query": state.user_query,
        },
        default=str,
    )

    try:
        result = await structured.ainvoke(
            [
                {"role": "system", "content": _read_system_prompt()},
                {"role": "user", "content": user_msg},
            ]
        )
    except Exception:  # noqa: BLE001 -- any LLM error -> deterministic fallback
        return None

    if isinstance(result, IAPDraftModel):
        return result
    try:
        return IAPDraftModel.model_validate(result)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Deterministic fallback draft
# --------------------------------------------------------------------------- #


def _deterministic_draft(
    state: AgentState,
    form: str,
    upstream: dict[str, Any],
) -> IAPDraftModel:
    op = state.operational_period
    incident_name = state.incident.name if state.incident else "(unknown incident)"

    objectives = [
        (
            "RECOMMEND life-safety first: protect responders and the public "
            "within the projected spread cone."
        ),
        (
            f"PROPOSE perimeter containment of {incident_name} at defensible "
            "terrain features identified by terrain_fuel."
        ),
        (
            "DRAFT structure-protection priorities for values-at-risk "
            "identified by values_at_risk."
        ),
    ]

    def _narr(key: str) -> str:
        return str(upstream.get(key, {}).get("narrative", ""))

    sections = [
        IAPSection(
            title="Situation Summary",
            body=(
                f"DRAFT situation summary for {incident_name} (op period {op}). "
                "Synthesis based on the seven upstream subagent outputs; refer "
                "to citations for individual reasoning traces."
            ),
        ),
        IAPSection(title="Weather & Wind (watch items)", body=_narr("weather_wind")),
        IAPSection(title="Terrain & Fuel", body=_narr("terrain_fuel")),
        IAPSection(title="Spread Projection", body=_narr("spread_simulation")),
        IAPSection(title="Values at Risk", body=_narr("values_at_risk")),
        IAPSection(
            title="PROPOSED Resource Posture",
            body=_narr("resource_recommendation"),
        ),
        IAPSection(
            title="DRAFT Evacuation Intent",
            body=_narr("evacuation_intelligence"),
        ),
        IAPSection(
            title="Routing & Staging (RECOMMENDED)",
            body=_narr("routing_staging"),
        ),
    ]

    assignments: list[Assignment] = []
    if form == "ICS-202-bundle":
        assignments = [
            Assignment(
                division="Div A (North Flank)",
                resources=["RECOMMEND: 2x Type 3 engines, 1x dozer"],
                work_assignment=(
                    "DRAFT: hold the containment line at the ridge identified "
                    "in terrain_fuel; coordinate with routing_staging for "
                    "ingress."
                ),
                special_instructions=(
                    "Refer to ICS-215A safety analysis; LCES required."
                ),
            ),
            Assignment(
                division="Div B (Structure Group)",
                resources=["RECOMMEND: 4x Type 1 engines"],
                work_assignment=(
                    "PROPOSE structure triage along the values_at_risk priority "
                    "list."
                ),
                special_instructions="Coordinate with evacuation_intelligence on phasing.",
            ),
        ]

    key_findings = [
        f"Form {form} drafted for op-period {op}.",
        (
            f"Synthesis aggregated "
            f"{sum(1 for v in upstream.values() if not v.get('missing'))} "
            "upstream subagent outputs."
        ),
        "All action verbs phrased as RECOMMEND / PROPOSE / DRAFT / SUGGEST.",
    ]

    safety_message = (
        "RECOMMEND LCES (Lookouts, Communications, Escape routes, Safety zones) "
        "compliance on every assignment. PROPOSE re-briefing of trigger points "
        "before each operational shift. IC approval required before any "
        "assignment becomes actionable."
    )

    return IAPDraftModel(
        form=form,
        operational_period=op,
        objectives=objectives,
        sections=sections,
        assignments=assignments,
        key_findings=key_findings,
        safety_message=safety_message,
    )


# --------------------------------------------------------------------------- #
# Citations
# --------------------------------------------------------------------------- #


def _build_citations(state: AgentState, mode: str) -> CitationBundle:
    datasets = [_ICS_SCHEMA_REF]
    for name in _UPSTREAM_AGENTS:
        ao = state.outputs.get(name)
        if ao is None:
            continue
        rid = ao.citation_bundle.reasoning_trace_id or ""
        datasets.append(
            Dataset(
                name=f"upstream:{name}",
                version=(rid[:8] if rid else "0"),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
    models = [Model(name=_DEFAULT_MODEL, version=mode)]
    return CitationBundle(
        datasets=datasets,
        models=models,
        reasoning_trace_id=str(uuid.uuid4()),
    )


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict[str, Any]:
    form = _form_for(state.operational_period)
    upstream = _condense_upstream(state)
    new_dissent = _build_dissent_log(state)

    draft_model = await _llm_synthesize(state, form, upstream, new_dissent)
    if draft_model is None:
        draft_model = _deterministic_draft(state, form, upstream)
        synthesis_mode = "deterministic-fallback"
    else:
        synthesis_mode = "llm-claude-sonnet-4-5"

    confidence, conf_driver = _aggregate_confidence(state)
    citations = _build_citations(state, synthesis_mode)

    existing_dissent = list(state.dissent_log or [])
    full_dissent = [*existing_dissent, *new_dissent]

    draft = {
        **draft_model.model_dump(),
        "drafted_at": datetime.now(timezone.utc).isoformat(),
        "synthesis_mode": synthesis_mode,
        "status": "DRAFT - pending IC approval",
        "verbs_constraint": "RECOMMEND / PROPOSE / DRAFT / SUGGEST only.",
        "dissent_log": full_dissent,
    }

    interrupt_payload: dict[str, Any] = {
        "type": "iap_approval",
        "form_type": form,
        "draft": draft,
        "dissent_log": full_dissent,
        "confidence": confidence,
        "citations": citations.model_dump(),
    }
    decision = request_human_decision("iap_approval", interrupt_payload) or {}

    verdict = str(decision.get("decision") or "approved").lower()
    edits = decision.get("edits") or {}

    if verdict == "approved":
        final_draft = draft
        payload: dict[str, Any] = {"iap_draft": final_draft}
    elif verdict == "edited":
        final_draft = {**draft, **edits, "status": "EDITED - approved by IC"}
        payload = {"iap_draft": final_draft}
    else:
        reject_reason = decision.get("reason") or "rejected without reason"
        new_dissent = [
            *new_dissent,
            {
                "agent": AGENT_NAME,
                "kind": "ic_rejection",
                "concern": f"IC rejected draft: {reject_reason}",
                "rationale": (
                    "Master IC produced a draft but IC declined approval; no "
                    "iap_draft committed to state."
                ),
            },
        ]
        final_draft = None
        payload = {"dissent_log_includes_reject_reason": True}

    payload["form"] = form
    payload["key_findings"] = draft_model.key_findings
    payload["dissent_log"] = [*existing_dissent, *new_dissent]

    narrative = (
        f"Master IC synthesized a draft {form} for operational period "
        f"{state.operational_period} from "
        f"{sum(1 for v in upstream.values() if not v.get('missing'))} upstream "
        f"subagent outputs (mode: {synthesis_mode}). IC verdict: {verdict}."
    )

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload=payload,
        confidence=confidence,
        confidence_driver=conf_driver,
        citation_bundle=citations,
    )

    state_patch: dict[str, Any] = {
        "outputs": {AGENT_NAME: output},
        "audit_log": [audit_entry("iap_approval", interrupt_payload, decision)],
    }
    if final_draft is not None:
        state_patch["iap_draft"] = final_draft
    if new_dissent:
        state_patch["dissent_log"] = new_dissent

    return state_patch


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _smoke_outputs() -> dict[str, AgentOutput]:
    """Stub all 7 upstream subagent outputs.

    values_at_risk: confidence 0.8 with empty structures_at_risk list.
    spread_simulation: confidence 0.6 with high_risk_zones populated.
    Together those trigger the conflict-dissent entry. resource_recommendation
    is set to 0.4 to also exercise the low-confidence dissent branch.
    """

    def _ao(
        name: str,
        confidence: float,
        narrative: str,
        payload: dict[str, Any] | None = None,
    ) -> AgentOutput:
        return AgentOutput(
            agent=name,
            narrative=narrative,
            payload=payload or {},
            confidence=confidence,
            confidence_driver="smoke-test stub",
            citation_bundle=CitationBundle(
                datasets=[Dataset(name="(stub)", version="0")],
                models=[Model(name="(stub)", version="0")],
                reasoning_trace_id=str(uuid.uuid4()),
            ),
        )

    return {
        "weather_wind": _ao(
            "weather_wind",
            0.8,
            "12-hr forecast: SW winds 15-25 mph, RH 12%, Red Flag in effect.",
            {"red_flag": True, "wind_dir_deg": 220, "wind_mph": 20},
        ),
        "terrain_fuel": _ao(
            "terrain_fuel",
            0.75,
            "Fuel model GR2/SH5 mix, 35-50% slope on north aspect.",
            {"fuel_models": ["GR2", "SH5"], "slope_pct_max": 50},
        ),
        "values_at_risk": _ao(
            "values_at_risk",
            0.8,
            "No critical structures identified inside the projected 12-hr cone.",
            {"structures_at_risk": [], "structures_in_cone": 0},
        ),
        "routing_staging": _ao(
            "routing_staging",
            0.7,
            "RECOMMEND staging at Hwy-49 turnout; 12-min ingress to head.",
            {"staging_lat": 39.1, "staging_lon": -120.9, "ingress_min": 12},
        ),
        "spread_simulation": _ao(
            "spread_simulation",
            0.6,
            "ROS 8 ch/hr head, flame length 12 ft; high-risk zones flagged.",
            {
                "high_risk_zones": ["Zone_A_North", "Zone_B_East"],
                "head_ros_chains_per_hr": 8,
                "flame_length_ft": 12,
            },
        ),
        "resource_recommendation": _ao(
            "resource_recommendation",
            0.4,
            "DRAFT resource posture; data sparse, low confidence.",
            {"draft_resources": ["2x Type 3", "1x dozer"]},
        ),
        "evacuation_intelligence": _ao(
            "evacuation_intelligence",
            0.65,
            "PROPOSE Zone A advisory now; Zone B watch within 6 hrs.",
            {"zones_advisory": ["Zone_A"], "zones_watch": ["Zone_B"]},
        ),
    }


def _smoke_state(op_period: int = 1) -> AgentState:
    from ..state import Incident

    return AgentState(
        incident=Incident(
            id="smoke-001",
            name="Smoke Test Fire",
            lat=39.1,
            lon=-120.9,
            acres=120.0,
            contained_pct=0.0,
            source="synthetic",
        ),
        operational_period=op_period,
        user_query="Smoke test for master_ic synthesis.",
        outputs=_smoke_outputs(),
    )


async def _smoke_main() -> None:
    """Run the smoke test with a stubbed HITL approval."""

    def _stub_decision(interrupt_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "approved", "actor": "smoke_test"}

    global request_human_decision  # noqa: PLW0603 -- intentional swap for smoke
    real = request_human_decision
    request_human_decision = _stub_decision  # type: ignore[assignment]
    try:
        for op in (1, 2):
            state = _smoke_state(op_period=op)
            patch = await run(state)
            ao = patch["outputs"][AGENT_NAME]
            draft = patch.get("iap_draft") or {}
            print(
                json.dumps(
                    {
                        "op_period": op,
                        "form": draft.get("form") or ao.payload.get("form"),
                        "confidence": ao.confidence,
                        "confidence_driver": ao.confidence_driver,
                        "narrative": ao.narrative,
                        "dissent_count": len(ao.payload.get("dissent_log", [])),
                        "dissent_kinds": [
                            d.get("kind")
                            for d in ao.payload.get("dissent_log", [])
                        ],
                        "synthesis_mode": draft.get("synthesis_mode"),
                        "citations": len(ao.citation_bundle.datasets),
                    },
                    indent=2,
                )
            )
    finally:
        request_human_decision = real  # type: ignore[assignment]


if __name__ == "__main__":
    asyncio.run(_smoke_main())
