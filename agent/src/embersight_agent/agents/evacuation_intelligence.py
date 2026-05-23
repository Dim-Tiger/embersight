"""Evacuation Intelligence subagent.

Pass-2: cross-reference Cal OES CA_EVACUATIONS, predicted spread cone, and
population/structure inventory to PROPOSE zone status changes (NORMAL →
WARNING → ORDER). Every proposed change raises an `evac_zone_change`
interrupt — EmberSight never publishes evacuation orders.
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "evacuation_intelligence"


async def run(state: AgentState) -> dict:
    return await stub_run(
        AGENT_NAME, "Evacuation Intelligence (proposed zone changes)", state
    )
