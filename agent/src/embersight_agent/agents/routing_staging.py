"""Routing & Staging subagent.

Pass-2: OSMnx + networkx for ingress/egress routes; score candidate staging
areas (paved surface, water proximity, comms LOS proxy). Confidence based
on OSM coverage density in the AOI.
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "routing_staging"


async def run(state: AgentState) -> dict:
    return await stub_run(AGENT_NAME, "Routing & Staging analysis", state)
