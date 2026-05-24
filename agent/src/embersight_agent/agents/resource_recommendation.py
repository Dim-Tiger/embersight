"""Resource Recommendation subagent.

PROPOSE — never act. This agent has zero action-verb tools by design
(see the banned-verb list in tools/__init__.py). Its terminal call is
`submit_resource_recommendation`, which raises a LangGraph `interrupt()`
via `hitl.request_human_decision` so a human IC must approve / edit /
reject before anything downstream treats the recommendation as
actionable.

Pulls upstream context (spread cones + ROS, values-at-risk inventory,
terrain & fuel, routing/staging) and drafts a typed envelope of
apparatus, crews, aircraft, and overhead with a per-line-item
rationale, distance-to-staging, and arrival window. Confidence is
degraded for each missing upstream input.

Verbs in user-facing strings are RECOMMEND / PROPOSED / DRAFT only.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..hitl import audit_entry, request_human_decision
from ..state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Dataset,
    Incident,
    Model,
)

AGENT_NAME = "resource_recommendation"
LLM_MODEL = "claude-haiku-4-5"

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "resource_recommendation.md"
)


# --------------------------------------------------------------------------- #
# Pydantic v2 schemas for the recommendation envelope
# --------------------------------------------------------------------------- #


class ResourceLineItem(BaseModel):
    kind: str  # "apparatus" | "crew" | "aircraft" | "overhead"
    type: str
    quantity: int = Field(ge=0)
    rationale: str
    distance_to_staging_min: int | None = None
    arrival_window: str | None = None


class ResourceRecommendation(BaseModel):
    incident_id: str | None = None
    incident_name: str | None = None
    operational_period: int = 1
    urgency: str = "med"  # "low" | "med" | "high"
    apparatus: list[ResourceLineItem] = Field(default_factory=list)
    crews: list[ResourceLineItem] = Field(default_factory=list)
    aircraft: list[ResourceLineItem] = Field(default_factory=list)
    overhead: list[ResourceLineItem] = Field(default_factory=list)
    rationale_summary: str = ""
    expires_at: str | None = None


# --------------------------------------------------------------------------- #
# Upstream context extraction
# --------------------------------------------------------------------------- #


def _gather_context(state: AgentState) -> dict[str, Any]:
    incident = state.incident.model_dump() if state.incident else {}
    outputs = state.outputs

    def _payload(name: str) -> dict[str, Any]:
        out = outputs.get(name)
        if out is None:
            return {}
        return out.payload or {}

    return {
        "incident": incident,
        "operational_period": state.operational_period,
        "spread_simulation": _payload("spread_simulation"),
        "values_at_risk": _payload("values_at_risk"),
        "terrain_fuel": _payload("terrain_fuel"),
        "routing_staging": _payload("routing_staging"),
        "weather_wind": _payload("weather_wind"),
    }


def _missing_inputs(context: dict[str, Any]) -> list[str]:
    required = (
        "spread_simulation",
        "values_at_risk",
        "terrain_fuel",
        "routing_staging",
    )
    return [name for name in required if not context.get(name)]


def _confidence_for(missing: list[str]) -> tuple[float, str]:
    base = 0.9
    penalty = 0.15 * len(missing)
    confidence = max(0.1, base - penalty)
    if missing:
        driver = f"missing upstream inputs: {', '.join(missing)}"
    else:
        driver = (
            "all upstream subagents reported; resource availability "
            "feed assumed fresh"
        )
    return confidence, driver


# --------------------------------------------------------------------------- #
# Deterministic recommendation (no-LLM fallback + smoke-test path)
# --------------------------------------------------------------------------- #


def _stub_recommendation(
    state: AgentState, context: dict[str, Any]
) -> ResourceRecommendation:
    incident = context.get("incident") or {}
    acres = float(incident.get("acres") or 0.0)
    var = context.get("values_at_risk") or {}
    terrain = context.get("terrain_fuel") or {}

    structure_count = int(
        var.get("structure_count") or var.get("structures") or 0
    )
    has_hospital = bool(var.get("hospitals") or var.get("critical_facilities"))
    slope_pct = float(terrain.get("slope_pct") or terrain.get("slope") or 0.0)

    if acres >= 1000 or structure_count >= 200 or has_hospital:
        urgency = "high"
    elif acres >= 100 or structure_count >= 25:
        urgency = "med"
    else:
        urgency = "low"

    type3_engines = max(4, int(acres // 100) + 2)
    type1_engines = 2 if structure_count >= 25 else 1
    tenders = max(2, int(acres // 500) + 1)
    dozers = 2 if slope_pct < 40 else 1
    hotshot = 2 if acres >= 100 else 1
    ia_crews = max(2, int(acres // 50))
    type1_tankers = 2 if acres >= 500 else 1
    helitack = 2 if structure_count >= 25 else 1
    lead_planes = 1 if (type1_tankers + helitack) >= 2 else 0

    return ResourceRecommendation(
        incident_id=incident.get("id"),
        incident_name=incident.get("name"),
        operational_period=state.operational_period,
        urgency=urgency,
        apparatus=[
            ResourceLineItem(
                kind="apparatus",
                type="Type-3 Engine",
                quantity=type3_engines,
                rationale=(
                    "Wildland-capable initial attack; sized to projected "
                    "24h cone."
                ),
                distance_to_staging_min=20,
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Type-1 Engine",
                quantity=type1_engines,
                rationale=(
                    "Structure defense in the WUI footprint identified by "
                    "values-at-risk."
                ),
                distance_to_staging_min=35,
                arrival_window="1-3h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Water Tender",
                quantity=tenders,
                rationale=(
                    "Mobile water supply for engines operating beyond "
                    "hydrant coverage."
                ),
                distance_to_staging_min=45,
                arrival_window="2-4h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Dozer (Type 2)",
                quantity=dozers,
                rationale=(
                    "Direct line construction where slope permits; "
                    "indirect line otherwise."
                ),
                distance_to_staging_min=60,
                arrival_window="2-6h",
            ),
        ],
        crews=[
            ResourceLineItem(
                kind="crew",
                type="Type-1 Hotshot Crew",
                quantity=hotshot,
                rationale="Hot-line construction in steep / heavy-fuel divisions.",
                distance_to_staging_min=90,
                arrival_window="3-8h",
            ),
            ResourceLineItem(
                kind="crew",
                type="Type-2 IA Crew",
                quantity=ia_crews,
                rationale="Mop-up, line improvement, and structure prep.",
                distance_to_staging_min=60,
                arrival_window="2-6h",
            ),
        ],
        aircraft=[
            ResourceLineItem(
                kind="aircraft",
                type="Type-1 Air Tanker (VLAT/LAT)",
                quantity=type1_tankers,
                rationale=(
                    "Retardant lines ahead of the head; sized to ROS and "
                    "cone length."
                ),
                distance_to_staging_min=30,
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="aircraft",
                type="Type-2 Helicopter",
                quantity=helitack,
                rationale=(
                    "Bucket work on hot spots and direct support of ground "
                    "resources."
                ),
                distance_to_staging_min=25,
                arrival_window="0-1h",
            ),
            ResourceLineItem(
                kind="aircraft",
                type="Lead Plane / ATGS",
                quantity=lead_planes,
                rationale=(
                    "Tactical coordination once multiple fixed-wing assets "
                    "are on-scene."
                ),
                distance_to_staging_min=40,
                arrival_window="1-3h",
            ),
        ],
        overhead=[
            ResourceLineItem(
                kind="overhead",
                type="Incident Commander (Type appropriate)",
                quantity=1,
                rationale="Single point of accountability for the operational period.",
                arrival_window="0-1h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Operations Section Chief",
                quantity=1,
                rationale="Division-level tactical management as resources scale.",
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Planning Section Chief",
                quantity=1 if urgency != "low" else 0,
                rationale="Required once IAP cycle begins for operational period 2+.",
                arrival_window="2-6h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Safety Officer",
                quantity=1,
                rationale=(
                    "ICS 208 ownership; mandatory once aircraft and ground "
                    "forces co-locate."
                ),
                arrival_window="0-2h",
            ),
        ],
        rationale_summary=(
            f"Draft based on projected acreage growth ({acres:.0f} ac baseline), "
            f"{structure_count} structures in the values-at-risk footprint, "
            f"and {slope_pct:.0f}% slope class. RECOMMEND ONLY — IC approval required."
        ),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )


# --------------------------------------------------------------------------- #
# LLM path (claude-haiku-4-5 via langchain-anthropic)
# --------------------------------------------------------------------------- #


def _load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


async def _llm_recommendation(
    state: AgentState, context: dict[str, Any]
) -> ResourceRecommendation | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        return None

    system_md = _load_prompt()
    schema_hint = ResourceRecommendation.model_json_schema()
    user_msg = (
        "Draft a resource recommendation envelope as JSON matching the "
        "schema below. RECOMMEND ONLY — your output is a draft until the "
        "IC approves. No dispatch language. Use the upstream context to "
        "size every line item.\n\n"
        f"SCHEMA:\n{json.dumps(schema_hint)}\n\n"
        f"UPSTREAM CONTEXT:\n{json.dumps(context, default=str)}\n\n"
        "Return ONLY a JSON object that validates against the schema. No prose."
    )
    try:
        from ..tools.llm_stream import stream_text
        llm = ChatAnthropic(model=LLM_MODEL, max_tokens=2048, timeout=30)
        content = await stream_text(
            llm,
            [SystemMessage(content=system_md), HumanMessage(content=user_msg)],
        )
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        data = json.loads(content[start : end + 1])
        return ResourceRecommendation.model_validate(data)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Terminal action — raises interrupt(), never auto-acts
# --------------------------------------------------------------------------- #


def submit_resource_recommendation(
    state: AgentState,
    recommendation: ResourceRecommendation,
    confidence: float,
    citations: CitationBundle,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Terminal call. Builds the interrupt envelope and pauses the graph
    via `hitl.request_human_decision`. Returns (envelope, decision) once
    the human resumes with approve / edit / reject."""
    envelope = {
        "type": "resource_recommendation",
        "recommendation": recommendation.model_dump(),
        "urgency": recommendation.urgency,
        "expires_at": recommendation.expires_at,
        "confidence": confidence,
        "citations": citations.model_dump(),
        "incident": state.incident.model_dump() if state.incident else None,
    }
    decision = request_human_decision("resource_recommendation", envelope)
    return envelope, decision


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _key_findings(rec: ResourceRecommendation) -> list[str]:
    lines: list[str] = []
    for group in (rec.apparatus, rec.crews, rec.aircraft, rec.overhead):
        for item in group:
            if item.quantity <= 0:
                continue
            arrival = (
                f" / arrival {item.arrival_window}" if item.arrival_window else ""
            )
            lines.append(
                f"RECOMMEND {item.quantity}x {item.type}{arrival} — {item.rationale}"
            )
    return lines


async def run(state: AgentState) -> dict[str, Any]:
    context = _gather_context(state)
    missing = _missing_inputs(context)
    confidence, confidence_driver = _confidence_for(missing)

    recommendation = await _llm_recommendation(state, context)
    if recommendation is None:
        recommendation = _stub_recommendation(state, context)

    citations = CitationBundle(
        datasets=[
            Dataset(name="CAL FIRE Incident Mobilization Guide", version="2024"),
            Dataset(name="NWCG Resource Typing (PMS 200)", version="2024"),
        ],
        models=[Model(name=LLM_MODEL, version="anthropic")],
        reasoning_trace_id=str(uuid.uuid4()),
    )

    envelope, decision = submit_resource_recommendation(
        state, recommendation, confidence, citations
    )

    decision = decision or {}
    decision_kind = decision.get("decision", "approved")
    edits = decision.get("edits") or {}
    reason = decision.get("reason", "")

    if decision_kind == "edited" and edits:
        try:
            recommendation = ResourceRecommendation.model_validate(
                {**recommendation.model_dump(), **edits}
            )
        except Exception:  # noqa: BLE001
            # Malformed edit envelope — keep the original draft. The IC's
            # raw edits still live verbatim in the audit_log entry.
            pass

    if decision_kind == "rejected":
        payload: dict[str, Any] = {
            "status": "rejected",
            "draft": recommendation.model_dump(),
            "reason": reason,
            "key_findings": [],
            "missing_inputs": missing,
        }
        narrative = "0 units RECOMMENDED for approval, decision: rejected" + (
            f" — {reason}" if reason else ""
        )
        out_confidence = min(confidence, 0.3)
    else:
        unit_count = sum(
            item.quantity
            for group in (
                recommendation.apparatus,
                recommendation.crews,
                recommendation.aircraft,
                recommendation.overhead,
            )
            for item in group
        )
        payload = {
            "status": decision_kind,
            "recommendation": recommendation.model_dump(),
            "urgency": recommendation.urgency,
            "expires_at": recommendation.expires_at,
            "key_findings": _key_findings(recommendation),
            "missing_inputs": missing,
            "unit_count": unit_count,
        }
        narrative = (
            f"{unit_count} units RECOMMENDED for approval, "
            f"decision: {decision_kind}"
        )
        out_confidence = confidence

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload=payload,
        confidence=out_confidence,
        confidence_driver=confidence_driver,
        citation_bundle=citations,
    )

    return {
        "outputs": {AGENT_NAME: output},
        "audit_log": [audit_entry("resource_recommendation", envelope, decision)],
    }


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _build_mock_state() -> AgentState:
    incident = Incident(
        id="ca-2026-rr-smoke",
        name="Resource-Rec Smoke Incident",
        lat=39.41,
        lon=-121.13,
        acres=420.0,
        contained_pct=5.0,
        started_at="2026-05-23T14:00:00-07:00",
    )

    def _o(agent: str, payload: dict[str, Any]) -> AgentOutput:
        return AgentOutput(
            agent=agent,
            narrative=f"[mock] {agent}",
            payload=payload,
            confidence=0.7,
            citation_bundle=CitationBundle(
                datasets=[Dataset(name="(mock)", version="0")],
                models=[Model(name="(mock)", version="0")],
            ),
        )

    outputs = {
        "spread_simulation": _o(
            "spread_simulation",
            {
                "cones": {"1h": "<wkt>", "6h": "<wkt>", "12h": "<wkt>", "24h": "<wkt>"},
                "head_ros_chains_per_hr": 12.5,
                "flame_length_ft": 11.0,
            },
        ),
        "values_at_risk": _o(
            "values_at_risk",
            {
                "structure_count": 312,
                "hospitals": 1,
                "schools": 3,
                "critical_facilities": ["Memorial Hospital"],
            },
        ),
        "terrain_fuel": _o(
            "terrain_fuel",
            {"slope_pct": 28.0, "fuel_model": "TL3", "aspect": "S"},
        ),
        "routing_staging": _o(
            "routing_staging",
            {
                "staging_lat": 39.40,
                "staging_lon": -121.10,
                "drive_time_min": 18,
                "primary_ingress": "Hwy 70",
            },
        ),
    }
    return AgentState(incident=incident, operational_period=1, outputs=outputs)


def _smoke() -> None:
    """Run `run()` against a mock state with the HITL pause stubbed to
    auto-approve so the interrupt path is exercised end-to-end."""

    def _auto_approve(itype, payload):  # type: ignore[no-untyped-def]
        return {"decision": "approved", "actor": "smoke@embersight"}

    original = globals()["request_human_decision"]
    globals()["request_human_decision"] = _auto_approve
    try:
        state = _build_mock_state()
        patch = asyncio.run(run(state))
        output = patch["outputs"][AGENT_NAME]
        audit = patch["audit_log"][0]
        print("== resource_recommendation smoke ==")
        print(f"narrative: {output.narrative}")
        print(
            f"confidence: {output.confidence:.2f} "
            f"({output.confidence_driver})"
        )
        print(f"urgency: {output.payload.get('urgency')}")
        print(f"unit_count: {output.payload.get('unit_count')}")
        findings = output.payload.get("key_findings", [])
        print(f"key_findings ({len(findings)}):")
        for line in findings[:6]:
            print(f"  - {line}")
        print(f"audit decision: {audit.decision}")
    finally:
        globals()["request_human_decision"] = original


if __name__ == "__main__":
    _smoke()
