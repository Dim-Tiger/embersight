"""Spread Simulation subagent.

Pass-2: Pyretechnics surface_fire ROS, Anderson elliptical cone, Monte Carlo
N=200 perturbing wind speed/direction and fuel moisture. Outputs 1/6/12/24h
probability-of-burn polygons. Interrupts on trigger-point violations.
"""

from __future__ import annotations

from ..hitl import audit_entry, request_human_decision
from ..state import AgentState
from ._stub import make_stub_output

AGENT_NAME = "spread_simulation"


async def run(state: AgentState) -> dict:
    # Pass-1: no real model. Build the stub output and skip the
    # trigger-point interrupt; pass-2 will re-enable it once ROS thresholds
    # are wired.
    output = make_stub_output(
        agent=AGENT_NAME,
        role="Spread simulation (Pyretechnics + Monte Carlo)",
        state=state,
        confidence=0.55,
        confidence_driver="ensemble spread (stubbed)",
        extra_payload={
            "cones": {"1h": None, "6h": None, "12h": None, "24h": None},
            "head_ros_chains_per_hr": None,
            "flame_length_ft": None,
        },
    )

    patch: dict = {"outputs": {AGENT_NAME: output}}

    # Demo hook: trigger-point interrupt path lives here. Disabled in pass-1
    # so the smoke test reaches Master IC without an extra approval round.
    trigger_breached = False
    if trigger_breached:
        payload = {"agent": AGENT_NAME, "reason": "stub", "cone_summary": {}}
        decision = request_human_decision("trigger_point_violation", payload)
        patch["audit_log"] = [
            audit_entry("trigger_point_violation", payload, decision)
        ]

    return patch
