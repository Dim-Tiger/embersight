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
    spread = context.get("spread_simulation") or {}

    # --- values-at-risk count (real upstream shape: payload.rollup.*) ---
    rollup = var.get("rollup") or {}
    tallies = var.get("tallies") or {}
    structure_count = int(
        rollup.get("structure_count")
        or tallies.get("structures")
        or var.get("structure_count")
        or var.get("structures")
        or 0
    )
    has_hospital = bool(
        rollup.get("hospitals")
        or rollup.get("critical_facilities")
        or var.get("hospitals")
        or var.get("critical_facilities")
    )

    # --- fuel hazard (real upstream shape: terrain_fuel.fuel_model / slope_deg) ---
    fuel_model = (
        terrain.get("fuel_model") or terrain.get("fbfm40") or "unknown fuel model"
    )
    slope_pct = float(
        terrain.get("slope_pct")
        or terrain.get("slope_deg")
        or terrain.get("slope")
        or 0.0
    )
    flame_length_ft = float(spread.get("flame_length_ft") or 0.0)
    if flame_length_ft >= 11.0 or slope_pct >= 40:
        fuel_hazard = "extreme"
    elif flame_length_ft >= 8.0 or slope_pct >= 25:
        fuel_hazard = "high"
    elif flame_length_ft >= 4.0:
        fuel_hazard = "moderate"
    else:
        fuel_hazard = "low"

    # --- spread cone size (real upstream shape: spread_simulation.burn_area_24h_km2_p25) ---
    cone_area_km2 = float(spread.get("burn_area_24h_km2_p25") or 0.0)
    head_ros = float(spread.get("head_ros_chains_per_hr") or 0.0)
    if cone_area_km2 > 0:
        cone_size_str = f"{cone_area_km2:.1f} km² @24h (p25 cone)"
    elif head_ros > 0:
        cone_size_str = f"head ROS {head_ros:.1f} ch/hr (cone area not reported)"
    else:
        cone_size_str = "cone size not reported"

    var_str = (
        f"{structure_count} structures"
        + (" incl. hospital" if has_hospital else "")
        if structure_count or has_hospital
        else "VAR count not reported"
    )
    fuel_str = f"{fuel_model} / {fuel_hazard} hazard ({slope_pct:.0f}% slope)"
    cite = f"[cone={cone_size_str}; VAR={var_str}; fuel={fuel_str}]"

    if cone_area_km2 >= 5.0 or structure_count >= 200 or has_hospital:
        urgency = "high"
    elif cone_area_km2 >= 1.0 or acres >= 100 or structure_count >= 25:
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
                    f"Wildland-capable initial attack sized to the projected "
                    f"24h cone {cite}."
                ),
                distance_to_staging_min=20,
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Type-1 Engine",
                quantity=type1_engines,
                rationale=(
                    f"Structure defense in the WUI footprint {cite}."
                ),
                distance_to_staging_min=35,
                arrival_window="1-3h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Water Tender",
                quantity=tenders,
                rationale=(
                    f"Mobile water supply for engines operating beyond hydrant "
                    f"coverage {cite}."
                ),
                distance_to_staging_min=45,
                arrival_window="2-4h",
            ),
            ResourceLineItem(
                kind="apparatus",
                type="Dozer (Type 2)",
                quantity=dozers,
                rationale=(
                    f"Direct line where slope permits; indirect otherwise {cite}."
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
                rationale=(
                    f"Hot-line construction in steep / heavy-fuel divisions {cite}."
                ),
                distance_to_staging_min=90,
                arrival_window="3-8h",
            ),
            ResourceLineItem(
                kind="crew",
                type="Type-2 IA Crew",
                quantity=ia_crews,
                rationale=f"Mop-up, line improvement, and structure prep {cite}.",
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
                    f"Retardant lines ahead of the head, sized to ROS and "
                    f"cone length {cite}."
                ),
                distance_to_staging_min=30,
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="aircraft",
                type="Type-2 Helicopter",
                quantity=helitack,
                rationale=(
                    f"Bucket work on hot spots and direct support of ground "
                    f"resources {cite}."
                ),
                distance_to_staging_min=25,
                arrival_window="0-1h",
            ),
            ResourceLineItem(
                kind="aircraft",
                type="Lead Plane / ATGS",
                quantity=lead_planes,
                rationale=(
                    f"Tactical coordination once multiple fixed-wing assets are "
                    f"on-scene {cite}."
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
                rationale=(
                    f"Single point of accountability for the operational period "
                    f"{cite}."
                ),
                arrival_window="0-1h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Operations Section Chief",
                quantity=1,
                rationale=(
                    f"Division-level tactical management as resources scale {cite}."
                ),
                arrival_window="0-2h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Planning Section Chief",
                quantity=1 if urgency != "low" else 0,
                rationale=(
                    f"Required once IAP cycle begins for operational period 2+ "
                    f"{cite}."
                ),
                arrival_window="2-6h",
            ),
            ResourceLineItem(
                kind="overhead",
                type="Safety Officer",
                quantity=1,
                rationale=(
                    f"ICS 208 ownership; mandatory once aircraft and ground "
                    f"forces co-locate {cite}."
                ),
                arrival_window="0-2h",
            ),
        ],
        rationale_summary=(
            f"Draft sized to spread cone ({cone_size_str}), "
            f"values-at-risk ({var_str}), and fuel hazard ({fuel_str}). "
            f"RECOMMEND ONLY — IC approval required."
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
        flat_lines = [
            item
            for group in (
                recommendation.apparatus,
                recommendation.crews,
                recommendation.aircraft,
                recommendation.overhead,
            )
            for item in group
            if item.quantity > 0
        ]
        unit_count = sum(item.quantity for item in flat_lines)
        # Flat list of {kind, quantity, rationale, ...} entries — the
        # canonical "what to bring" surface for downstream consumers and
        # the UI. The grouped envelope under `recommendation` is retained
        # for the ICS-201 cross-reference.
        recommendations_flat = [
            {
                "kind": item.kind,
                "type": item.type,
                "quantity": item.quantity,
                "rationale": item.rationale,
                "distance_to_staging_min": item.distance_to_staging_min,
                "arrival_window": item.arrival_window,
            }
            for item in flat_lines
        ]
        payload = {
            "status": decision_kind,
            "recommendation": recommendation.model_dump(),
            "recommendations": recommendations_flat,
            "urgency": recommendation.urgency,
            "expires_at": recommendation.expires_at,
            "rationale_summary": recommendation.rationale_summary,
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
    """Mock state with payload shapes that mirror the real upstream
    agents (see master_ic._mock_state_with_dissents for the reference
    convention)."""
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

    # Realistic shapes — mirror agents/spread_simulation.py:569,
    # agents/values_at_risk.py:412, agents/terrain_fuel.py:244,
    # agents/routing_staging.py:203.
    outputs = {
        "weather_wind": _o(
            "weather_wind",
            {"wind_dir_deg": 225, "wind_mph": 18, "gust_mph": 28, "rh_pct": 14},
        ),
        "spread_simulation": _o(
            "spread_simulation",
            {
                "key_findings": [
                    "Head ROS 12.5 ch/hr; 24h cone p25 ≈ 6.4 km²",
                ],
                "cones": {
                    "1h": {"type": "Polygon", "coordinates": [[[-121.13, 39.41]]]},
                    "6h": {"type": "Polygon", "coordinates": [[[-121.13, 39.41]]]},
                    "12h": {"type": "Polygon", "coordinates": [[[-121.13, 39.41]]]},
                    "24h": {"type": "Polygon", "coordinates": [[[-121.13, 39.41]]]},
                },
                "cone_bands": {
                    "24h": {"p25": 6.4, "p50": 8.1, "p75": 10.7, "p95": 14.2},
                },
                "head_ros_chains_per_hr": 12.5,
                "head_ros_fpm_mean": 13.8,
                "head_ros_fpm_std": 2.1,
                "flame_length_ft": 11.0,
                "burn_area_24h_km2_p25": 6.4,
                "trigger_breaches": [{"id": "Figueroa-Ranch", "t_min": 220}],
                "high_risk_zones": ["N4", "N5"],
                "n_mc_samples": 200,
                "fuel_model": "TL3",
                "wind_speed_mph": 18,
                "wind_dir_deg": 225,
                "fuel_moisture": {"1hr": 4, "10hr": 6, "100hr": 9, "live": 75},
            },
        ),
        "values_at_risk": _o(
            "values_at_risk",
            {
                "cone_source": "spread_simulation.cones.24h",
                "bbox": [-121.20, 39.35, -121.05, 39.48],
                "rollup": {
                    "structure_count": 312,
                    "hospitals": 1,
                    "schools": 3,
                    "critical_facilities": ["Memorial Hospital"],
                },
                "tallies": {"structures": 312, "parcels": 198, "roads_km": 24.5},
                "key_findings": [
                    "312 structures and 1 hospital inside the 24h cone footprint",
                ],
            },
        ),
        "terrain_fuel": _o(
            "terrain_fuel",
            {
                "fuel_model": "TL3 moderate load conifer litter",
                "slope_deg": 28.0,
                "aspect": "S",
                "elevation_m": 612,
            },
        ),
        "routing_staging": _o(
            "routing_staging",
            {
                "candidates": [
                    {
                        "name": "Hwy 70 Oroville-Quincy turnout",
                        "lat": 39.40,
                        "lon": -121.10,
                        "score": 0.84,
                        "drive_time_min": 18,
                    }
                ],
                "closures": [],
                "primary_ingress": "Hwy 70",
            },
        ),
    }
    return AgentState(incident=incident, operational_period=1, outputs=outputs)


def _smoke() -> None:
    """Run `run()` against a mock state with the HITL pause stubbed to
    auto-approve so the interrupt path is exercised end-to-end. Asserts
    the payload contract: concrete PROPOSED line items in
    payload.recommendations, citing cone / VAR / fuel."""

    def _auto_approve(itype, payload):  # type: ignore[no-untyped-def]
        return {"decision": "approved", "actor": "smoke@embersight"}

    original = globals()["request_human_decision"]
    globals()["request_human_decision"] = _auto_approve
    try:
        state = _build_mock_state()
        patch = asyncio.run(run(state))
        output = patch["outputs"][AGENT_NAME]
        audit = patch["audit_log"][0]

        # --- contract assertions --- #
        payload = output.payload
        assert payload.get("status") == "approved", payload.get("status")
        assert payload.get("unit_count", 0) > 0, "no units recommended"

        recs = payload.get("recommendations") or []
        assert isinstance(recs, list) and recs, (
            "payload.recommendations must be a non-empty list of "
            "{kind, quantity, rationale, ...} entries — got "
            f"{type(recs).__name__} len={len(recs)}"
        )
        required_keys = {"kind", "quantity", "rationale"}
        for entry in recs:
            missing_keys = required_keys - set(entry)
            assert not missing_keys, (
                f"recommendation entry missing keys {missing_keys}: {entry}"
            )
            assert entry["quantity"] > 0, f"zero-qty leaked into recommendations: {entry}"

        # Every rationale must cite cone / VAR / fuel signals.
        for entry in recs:
            rat = entry["rationale"].lower()
            assert "cone=" in rat, f"rationale missing cone citation: {entry}"
            assert "var=" in rat, f"rationale missing VAR citation: {entry}"
            assert "fuel=" in rat, f"rationale missing fuel citation: {entry}"

        summary = payload.get("rationale_summary", "")
        for token in ("cone", "values-at-risk", "fuel hazard"):
            assert token in summary.lower(), (
                f"rationale_summary missing {token!r}: {summary!r}"
            )

        # --- report --- #
        print("== resource_recommendation smoke ==")
        print(f"narrative: {output.narrative}")
        print(
            f"confidence: {output.confidence:.2f} "
            f"({output.confidence_driver})"
        )
        print(f"urgency: {payload.get('urgency')}")
        print(f"unit_count: {payload.get('unit_count')}")
        print(f"recommendations ({len(recs)}):")
        for entry in recs:
            print(
                f"  - {entry['quantity']}x {entry['type']} "
                f"({entry['kind']}, arrival {entry.get('arrival_window')})"
            )
            print(f"      rationale: {entry['rationale']}")
        print(f"rationale_summary: {summary}")
        findings = payload.get("key_findings", [])
        print(f"key_findings ({len(findings)}):")
        for line in findings[:6]:
            print(f"  - {line}")
        print(f"audit decision: {audit.decision}")
        print("OK: payload contract satisfied.")
    finally:
        globals()["request_human_decision"] = original


if __name__ == "__main__":
    _smoke()
