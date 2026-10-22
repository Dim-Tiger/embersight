"""Briefing-graph topology tests.

Every specialist is replaced with a tiny fake node so these tests run
offline in milliseconds. They pin the execution ORDER of the real
StateGraph wiring in graph.py, which is what decides whether the Master
IC sees a complete team or synthesizes from a half-finished one.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from embersight_agent import graph as graph_mod
from embersight_agent.state import AgentOutput, AgentState, Incident

SPECIALISTS = (
    "weather_wind",
    "terrain_fuel",
    "values_at_risk",
    "routing_staging",
    "spread_simulation",
    "resource_recommendation",
    "evacuation_intelligence",
)


def _fake_node(name: str, calls: list[tuple[str, set[str]]]):
    async def run(state: AgentState) -> dict:
        calls.append((name, set(state.outputs.keys())))
        return {
            "outputs": {
                name: AgentOutput(agent=name, narrative=f"fake {name}", confidence=0.8)
            }
        }

    return run


@pytest.fixture
def fake_team(monkeypatch):
    """Swap every node's run() for a recorder; returns the call log."""
    calls: list[tuple[str, set[str]]] = []
    for name in SPECIALISTS:
        module = getattr(graph_mod, name)
        monkeypatch.setattr(module, "run", _fake_node(name, calls))
    monkeypatch.setattr(graph_mod.master_ic, "run", _fake_node("master_ic", calls))
    return calls


def _run_briefing() -> None:
    compiled = graph_mod.build_briefing_graph().compile(checkpointer=InMemorySaver())
    state = AgentState(
        incident=Incident(id="T", name="Topology Fire", lat=34.5, lon=-119.7)
    )
    asyncio.run(
        compiled.ainvoke(state, config={"configurable": {"thread_id": "topology"}})
    )


def test_master_ic_runs_once_after_every_specialist(fake_team):
    _run_briefing()
    master_calls = [seen for name, seen in fake_team if name == "master_ic"]
    assert len(master_calls) == 1, (
        f"master_ic must run exactly once per briefing, ran {len(master_calls)}x"
    )
    missing = set(SPECIALISTS) - master_calls[0]
    assert not missing, f"master_ic ran before these specialists finished: {missing}"


def test_spread_waits_for_weather_and_terrain(fake_team):
    _run_briefing()
    spread_seen = [seen for name, seen in fake_team if name == "spread_simulation"]
    assert len(spread_seen) == 1
    assert {"weather_wind", "terrain_fuel"} <= spread_seen[0]


def test_every_specialist_runs_exactly_once(fake_team):
    _run_briefing()
    names = [name for name, _ in fake_team]
    for name in SPECIALISTS:
        assert names.count(name) == 1, f"{name} ran {names.count(name)}x"
