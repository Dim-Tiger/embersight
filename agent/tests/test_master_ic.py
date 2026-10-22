"""Master IC synthesis: confidence aggregation and dissent detection."""

from __future__ import annotations

import pytest

from embersight_agent.agents import master_ic
from embersight_agent.state import AgentOutput, AgentState

ALL = master_ic.UPSTREAM_AGENTS


def _state(conf: dict[str, float] | None = None, payloads: dict | None = None) -> AgentState:
    conf = conf or {}
    payloads = payloads or {}
    return AgentState(outputs={
        n: AgentOutput(agent=n, narrative=f"{n} ok", confidence=conf.get(n, 0.8),
                       payload=payloads.get(n, {}))
        for n in ALL
    })


def test_harmonic_mean_is_dragged_down_by_one_weak_input():
    value, _ = master_ic._harmonic_mean_confidence(_state({"routing_staging": 0.1}))
    arithmetic = (0.8 * 6 + 0.1) / 7
    assert value < arithmetic
    assert value == pytest.approx(7 / (6 / 0.8 + 1 / 0.1), abs=1e-3)


def test_missing_upstream_gives_zero_with_reason():
    state = _state()
    del state.outputs["values_at_risk"]
    value, driver = master_ic._harmonic_mean_confidence(state)
    assert value == 0.0 and "values_at_risk" in driver


def _spread_vs_values(state: AgentState) -> list[dict]:
    return [d for d in master_ic._detect_dissent(state)
            if d["source_agent"] == "spread_simulation" and d["conflicting_agent"] == "values_at_risk"]


def test_spread_vs_values_dissent_fires_on_fast_fire_with_empty_cone():
    state = _state(payloads={
        "spread_simulation": {"head_ros_chains_per_hr": 14.0, "cone_impact": {"critical_facilities_total": 0}},
        "values_at_risk": {"rollup": {"structures_total": 0}},
    })
    assert len(_spread_vs_values(state)) == 1


def test_word_critical_in_a_payload_key_is_not_high_risk():
    state = _state(payloads={
        "spread_simulation": {"head_ros_chains_per_hr": 1.0, "cone_impact": {"critical_facilities_total": 0}},
        "values_at_risk": {"rollup": {"structures_total": 0}},
    })
    assert _spread_vs_values(state) == []


def test_unknown_structure_count_is_not_an_empty_cone():
    state = _state(payloads={
        "spread_simulation": {"head_ros_chains_per_hr": 14.0},
        "values_at_risk": {"rollup": {"structures_total": None}},
    })
    assert _spread_vs_values(state) == []


def test_deterministic_draft_never_uses_forbidden_verbs():
    draft = master_ic._deterministic_synthesis(_state(), "ICS-201", [])
    assert master_ic._forbidden_hits(str(draft)) == []
    bundle = master_ic._deterministic_synthesis(_state(), "ICS-202-bundle", [])
    assert master_ic._forbidden_hits(str(bundle)) == []
