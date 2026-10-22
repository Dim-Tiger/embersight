"""Resource Recommendation: HITL status and upstream contract."""

from __future__ import annotations

import asyncio

from embersight_agent.agents import resource_recommendation as rr
from embersight_agent.state import AgentOutput, AgentState, Incident


def _out(agent: str, payload: dict) -> AgentOutput:
    return AgentOutput(agent=agent, narrative=f"{agent} ok", payload=payload, confidence=0.8)


def _state(mode: str = "briefing", acres: float = 120.0, contained: float | None = 0.0,
           var_rollup: dict | None = None) -> AgentState:
    return AgentState(
        mode=mode,
        incident=Incident(
            id="RR-1", name="Resource Fire", lat=34.5, lon=-119.7,
            acres=acres, contained_pct=contained,
        ),
        outputs={
            "spread_simulation": _out(
                "spread_simulation",
                {"burn_area_24h_km2_p25": 0.5, "head_ros_chains_per_hr": 4.0, "flame_length_ft": 3.0},
            ),
            "values_at_risk": _out("values_at_risk", {"rollup": var_rollup or {}}),
            "terrain_fuel": _out("terrain_fuel", {"fuel_model": "GR2", "slope_pct": 10.0}),
            "routing_staging": _out("routing_staging", {"candidates": []}),
        },
    )


def _no_interrupt(monkeypatch) -> list:
    calls: list = []

    def fake(kind, payload):  # noqa: ANN001
        calls.append(kind)
        return {"decision": "approved"}

    monkeypatch.setattr(rr, "request_human_decision", fake)
    return calls


def test_briefing_draft_is_not_reported_as_approved(monkeypatch):
    calls = _no_interrupt(monkeypatch)
    patch = asyncio.run(rr.run(_state("briefing")))
    out = patch["outputs"]["resource_recommendation"]

    assert calls == [], "briefing mode must not pause; the IAP approval gates it"
    assert out.payload["status"] == "draft_pending_iap_approval"
    assert "approved" not in out.narrative.lower().replace("approval", "")
    # No interrupt happened, so there is no decision to audit. An audit row
    # claiming an approval nobody gave would be worse than none.
    assert not patch.get("audit_log")


def test_chat_mode_unrecognised_decision_is_not_approval(monkeypatch):
    monkeypatch.setattr(rr, "request_human_decision", lambda kind, payload: {})
    patch = asyncio.run(rr.run(_state("chat")))
    out = patch["outputs"]["resource_recommendation"]
    assert out.payload["status"] != "approved"
    assert out.payload.get("key_findings") == []
    assert len(patch["audit_log"]) == 1


def test_reads_values_at_risk_rollup_keys():
    """VAR publishes rollup.structures_total / hospitals_count; RR must read them."""
    state = _state(var_rollup={"structures_total": 300, "residential_count": 250,
                               "hospitals_count": 1})
    rec = rr._stub_recommendation(state, rr._gather_context(state))
    assert rec.urgency == "high"
    assert "300 structures incl. hospital" in rec.rationale_summary


def test_sizes_to_uncontained_fire_not_total_acres():
    open_fire = rr._stub_recommendation(*_ctx(_state(acres=6788, contained=0.0)))
    contained = rr._stub_recommendation(*_ctx(_state(acres=6788, contained=1.0)))

    def qty(rec, type_name):
        return next(i.quantity for g in (rec.apparatus, rec.crews) for i in g if i.type == type_name)

    assert qty(contained, "Type-2 IA Crew") < qty(open_fire, "Type-2 IA Crew")
    assert qty(contained, "Type-3 Engine") < qty(open_fire, "Type-3 Engine")
    assert "contained" in contained.rationale_summary


def _ctx(state: AgentState):
    return state, rr._gather_context(state)


def test_slope_in_degrees_is_converted_to_percent():
    state = _state()
    state.outputs["terrain_fuel"] = _out("terrain_fuel", {"fuel_model": "GR2", "slope_deg": 28.0})
    rec = rr._stub_recommendation(state, rr._gather_context(state))
    # tan(28°) = 53% slope, which is "extreme" (>= 40%); read as 28% it was "high".
    assert "extreme hazard (53% slope)" in rec.rationale_summary
