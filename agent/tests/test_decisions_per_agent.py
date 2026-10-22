"""One reject / edit test for every agent that resumes from interrupt().

Every agent reads the resume value through hitl.decision_kind, which accepts
both spellings ("rejected" / "reject"). These tests pin that each consumer
honours a rejection and reads the edit fields the web UI sends.
"""

from __future__ import annotations

import asyncio

import pytest

from embersight_agent.agents import evacuation_intelligence as ei
from embersight_agent.agents import master_ic, spread_simulation
from embersight_agent.agents import resource_recommendation as rr
from embersight_agent.hitl import decision_kind
from embersight_agent.state import AgentOutput, AgentState, Incident

from . import test_evac_and_zones as tz
from .test_hitl_decisions import _master_state_with_pending_draft, _spread_state_with_trigger_point
from .test_resource_recommendation import _state as rr_state


def _ic_says(monkeypatch, module, decision) -> None:
    monkeypatch.setattr(module, "request_human_decision", lambda kind, payload: decision)


@pytest.mark.parametrize("spelling", ["rejected", "reject", "REJECTED", " Reject "])
def test_decision_helper_accepts_both_spellings(spelling):
    assert decision_kind({"decision": spelling}) == "rejected"


def test_decision_helper_short_forms():
    assert decision_kind({"decision": "approve"}) == "approved"
    assert decision_kind({"decision": "edit"}) == "edited"
    assert decision_kind({"decision": "aprove"}) == "unrecognized"
    assert decision_kind("approved") == "unrecognized"


# ---- spread_simulation (trigger_point_violation) ------------------------------


@pytest.mark.parametrize("spelling", ["rejected", "reject"])
def test_spread_rejection_withholds_cones(monkeypatch, spelling):
    _ic_says(monkeypatch, spread_simulation, {"decision": spelling, "reason": "lookout reports hold"})
    out = asyncio.run(spread_simulation.run(_spread_state_with_trigger_point()))
    assert out["outputs"]["spread_simulation"].payload["cones"]["24h"] is None


# ---- master_ic (iap_approval) ------------------------------------------------------


def test_iap_rejection_is_not_published_and_reason_is_logged(monkeypatch):
    _ic_says(monkeypatch, master_ic, {"decision": "reject", "reason": "wrong fuel model"})
    patch = asyncio.run(master_ic.approve_iap(_master_state_with_pending_draft()))
    assert patch["outputs"]["master_ic"].payload["decision"] == "rejected"
    assert not patch.get("iap_draft")
    assert any(d["claim_b"] == "wrong fuel model" for d in patch["dissent_log"])


def test_iap_edit_publishes_the_ics_edited_draft(monkeypatch):
    edited = {"form": "ICS-201", "objectives": ["Life safety (IC edit)"]}
    _ic_says(monkeypatch, master_ic, {"decision": "edited", "edited_draft": edited})
    patch = asyncio.run(master_ic.approve_iap(_master_state_with_pending_draft()))
    assert patch["iap_draft"] == edited


# ---- resource_recommendation (chat-mode interrupt) ---------------------------------


def test_resource_rejection_is_honoured(monkeypatch):
    _ic_says(monkeypatch, rr, {"decision": "reject", "reason": "already committed"})
    out = asyncio.run(rr.run(rr_state("chat")))["outputs"]["resource_recommendation"]
    assert out.payload["status"] == "rejected"
    assert out.payload["reason"] == "already committed"
    assert out.payload["key_findings"] == []


def test_resource_quantity_edits_are_applied(monkeypatch):
    heli = {"kind": "aircraft", "type": "Type-2 Helicopter", "quantity": 3, "rationale": "IC edit"}
    _ic_says(monkeypatch, rr, {"decision": "edited", "edits": {"aircraft": [heli]}})
    out = asyncio.run(rr.run(rr_state("chat")))["outputs"]["resource_recommendation"]
    assert out.payload["status"] == "edited"
    assert [(a["type"], a["quantity"]) for a in out.payload["recommendation"]["aircraft"]] == [
        ("Type-2 Helicopter", 3)
    ]


# ---- evacuation_intelligence (evac_zone_change) ------------------------------------


def _evac_final_status(monkeypatch, decision) -> dict:
    async def zones(bbox):  # noqa: ANN001
        return [tz._zone("Z1", tz._square(tz.LON, tz.LAT, 0.005))]

    async def no_overlay(bbox):  # noqa: ANN001
        return {}

    monkeypatch.setattr(tz.zc, "fetch_static_zones_in_bbox", zones)
    monkeypatch.setattr(tz.evac_tools, "get_active_status_overlay", no_overlay)
    _ic_says(monkeypatch, ei, decision)
    spread = AgentOutput(agent="spread_simulation", narrative="", payload={
        "cones": {"6h": tz._disk(0.01), "12h": tz._disk(0.05), "24h": tz._disk(0.1)}})
    state = AgentState(incident=Incident(id="E", name="Evac", lat=tz.LAT, lon=tz.LON),
                       outputs={"spread_simulation": spread})
    out = asyncio.run(ei.run(state))["outputs"]["evacuation_intelligence"]
    return out.payload["zone_changes"][0]


def test_evac_rejection_keeps_the_current_status(monkeypatch):
    change = _evac_final_status(monkeypatch, {"decision": "reject", "reason": "behind the line"})
    assert change["proposed_status"] == "ORDER"
    assert change["final_status"] == "NORMAL"


def test_evac_edit_uses_the_ics_status(monkeypatch):
    change = _evac_final_status(monkeypatch, {"decision": "edited", "edits": {"proposed_status": "warning"}})
    assert change["final_status"] == "WARNING"
