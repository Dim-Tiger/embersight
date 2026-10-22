"""Human-in-the-loop decision handling.

The web ApprovalQueue resumes interrupts with
``{"decision": "approved" | "edited" | "rejected", ...}`` (see hitl.py).
These tests pin how each agent reads that envelope, and that anything it
does not recognise is treated as NOT approved.
"""

from __future__ import annotations

import asyncio

import pytest

from embersight_agent.agents import master_ic, spread_simulation
from embersight_agent.state import AgentOutput, AgentState, Incident


def _out(agent: str, payload: dict, confidence: float = 0.8) -> AgentOutput:
    return AgentOutput(agent=agent, narrative=f"{agent} ok", payload=payload, confidence=confidence)


def _spread_state_with_trigger_point() -> AgentState:
    # Wind FROM the west pushes the head east; the trigger point sits
    # ~700 m east of the ignition, well inside the 1h cone.
    incident = Incident(
        id="T-1",
        name="Trigger Fire",
        lat=34.5,
        lon=-119.7,
        source="synthetic",
        raw={"trigger_points": [{"id": "Ranch-Gate", "lat": 34.5, "lon": -119.692}]},
    )
    return AgentState(
        incident=incident,
        outputs={
            "weather_wind": _out(
                "weather_wind",
                {"wind_speed_mph": 20.0, "wind_dir_deg": 270.0, "fuel_moisture": {"1h": 5.0}},
            ),
            "terrain_fuel": _out(
                "terrain_fuel", {"fuel_model": "SH5", "slope_pct": 20.0, "aspect_deg": 90.0}
            ),
        },
    )


def _capture(monkeypatch, module, decision: dict) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []

    def fake(kind, payload):  # noqa: ANN001
        seen.append((kind, payload))
        return decision

    monkeypatch.setattr(module, "request_human_decision", fake)
    return seen


def test_spread_trigger_breach_reject_is_honoured(monkeypatch):
    seen = _capture(monkeypatch, spread_simulation, {"decision": "rejected", "actor": "ic"})
    patch = asyncio.run(spread_simulation.run(_spread_state_with_trigger_point()))

    assert [k for k, _ in seen] == ["trigger_point_violation"]
    out = patch["outputs"]["spread_simulation"]
    assert out.payload["cones"]["24h"] is None, "a rejected spread run must not publish cones"
    assert "rejected" in out.narrative.lower()
    assert len(patch["audit_log"]) == 1


def test_spread_trigger_breach_edit_uses_ic_cones(monkeypatch):
    ic_cone = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    _capture(
        monkeypatch,
        spread_simulation,
        {"decision": "edited", "edits": {"cones": {"24h": ic_cone}}},
    )
    patch = asyncio.run(spread_simulation.run(_spread_state_with_trigger_point()))
    assert patch["outputs"]["spread_simulation"].payload["cones"] == {"24h": ic_cone}


def _master_state() -> AgentState:
    names = (
        "weather_wind",
        "terrain_fuel",
        "values_at_risk",
        "routing_staging",
        "spread_simulation",
        "resource_recommendation",
        "evacuation_intelligence",
    )
    return AgentState(
        incident=Incident(id="M-1", name="Synthesis Fire", lat=34.5, lon=-119.7),
        outputs={n: _out(n, {}) for n in names},
    )


@pytest.mark.parametrize("decision", [{"decision": "approve"}, {}, {"decision": None}])
def test_master_ic_unrecognised_decision_is_not_approval(monkeypatch, decision):
    _capture(monkeypatch, master_ic, decision)
    patch = asyncio.run(master_ic.approve_iap(_master_state_with_pending_draft()))
    out = patch["outputs"]["master_ic"]
    assert out.payload["decision"] != "approved"
    assert not patch.get("iap_draft"), "an unrecognised decision must not publish the IAP"


def test_master_ic_approved_decision_keeps_the_draft_the_ic_saw(monkeypatch):
    _capture(monkeypatch, master_ic, {"decision": "approved", "actor": "ic"})
    pending = _master_state_with_pending_draft()
    shown = pending.outputs["master_ic"].payload["iap_draft"]
    patch = asyncio.run(master_ic.approve_iap(pending))
    assert patch["iap_draft"] == shown
    assert patch["outputs"]["master_ic"].payload["decision"] == "approved"
    assert len(patch["audit_log"]) == 1


def _master_state_with_pending_draft() -> AgentState:
    state = _master_state()
    synth = asyncio.run(master_ic.run(state))
    return state.model_copy(update={"outputs": {**state.outputs, **synth["outputs"]}})


def test_master_ic_synthesis_does_not_interrupt(monkeypatch):
    seen = _capture(monkeypatch, master_ic, {"decision": "approved"})
    patch = asyncio.run(master_ic.run(_master_state()))
    assert seen == [], "synthesis must not pause; the separate approval node does"
    out = patch["outputs"]["master_ic"]
    assert out.payload["decision"] == "pending_ic_approval"
    assert out.confidence > 0.0
