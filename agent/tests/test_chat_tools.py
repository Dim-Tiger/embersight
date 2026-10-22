"""Chat-mode consult tools: audit completeness and the no-dispatch guarantee."""

from __future__ import annotations

import asyncio
import inspect
import re
import types

from embersight_agent.agents import master_ic_chat
from embersight_agent.agents import tools as consult
from embersight_agent.hitl import audit_entry
from embersight_agent.state import AgentOutput, AgentState, Incident


def test_consult_forwards_the_specialists_audit_records(monkeypatch):
    record = audit_entry("resource_recommendation", {"x": 1}, {"decision": "approved"})

    async def fake_run(state):  # noqa: ANN001
        return {
            "outputs": {"resource_recommendation": AgentOutput(
                agent="resource_recommendation", narrative="ok")},
            "audit_log": [record],
        }

    monkeypatch.setitem(consult._SPECIALISTS, "resource_recommendation",
                        types.SimpleNamespace(run=fake_run))
    cmd = asyncio.run(consult._consult_impl(
        "resource_recommendation", True, AgentState(), "call-1", "refresh"))
    assert cmd.update["audit_log"] == [record]


def test_no_tool_can_dispatch_order_send_or_publish():
    """README guarantee: no tool named dispatch_* / order_* / send_* / publish_*."""
    names = [t.name for t in consult.ALL_TOOLS]
    banned = re.compile(r"^(dispatch|order|send|publish)_")
    assert names and not [n for n in names if banned.match(n)]


def test_every_interrupting_agent_writes_an_audit_record():
    """Static check: each module that calls request_human_decision also
    builds an audit_entry right after it (synthetic tool excepted, it is not
    exposed to the IC)."""
    from embersight_agent.agents import (
        evacuation_intelligence,
        master_ic,
        resource_recommendation,
        spread_simulation,
    )

    for mod in (evacuation_intelligence, master_ic, resource_recommendation, spread_simulation):
        src = inspect.getsource(mod)
        calls = len(re.findall(r"= request_human_decision\(", src))
        audits = len(re.findall(r"audit_entry\(", src))
        assert calls and audits >= calls, mod.__name__


def test_chat_briefing_reports_containment_as_percent():
    state = AgentState(incident=Incident(id="c", name="c", lat=0, lon=0, contained_pct=1.0))
    assert '"contained": "100% contained"' in master_ic_chat._state_briefing(state)
