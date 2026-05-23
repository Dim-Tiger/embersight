"""Terrain & Fuel subagent.

Pass-2: LANDFIRE FBFM40 + USGS DEM slope/aspect + canopy. Confidence based
on fuel-model purity (1 - entropy of FBFM40 class distribution in AOI).
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "terrain_fuel"


async def run(state: AgentState) -> dict:
    return await stub_run(AGENT_NAME, "Terrain & Fuel characterization", state)
