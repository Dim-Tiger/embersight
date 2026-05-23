"""Resource Recommendation subagent.

By design this agent has NO `dispatch_*` / `order_*` / `send_*` tools. Its
terminal tool is `submit_resource_recommendation` which raises an interrupt.
The verb in the UI is always RECOMMEND or PROPOSED, never Dispatch.
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "resource_recommendation"


async def run(state: AgentState) -> dict:
    return await stub_run(
        AGENT_NAME,
        "Resource RECOMMENDATIONS (proposed apparatus / crews / aircraft)",
        state,
    )
