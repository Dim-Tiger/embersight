"""Values-at-Risk subagent.

Pass-2: spatial-join MS Building Footprints + USA Structures + CMS hospitals
+ NCES schools + EIA transmission lines against the predicted spread cone.
Confidence penalized by footprint vintage age.
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "values_at_risk"


async def run(state: AgentState) -> dict:
    return await stub_run(AGENT_NAME, "Values-at-Risk inventory", state)
