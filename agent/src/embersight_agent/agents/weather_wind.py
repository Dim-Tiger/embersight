"""Weather & Wind subagent.

Pass-2 will pull HRRR + RTMA via Herbie and RAWS via SynopticPy, fuse them,
flag Red Flag warnings, and emit a 24-hour wind/RH/temp forecast for the
incident AOI. Confidence = HRRR/RTMA agreement on wind direction.
"""

from __future__ import annotations

from ..state import AgentState
from ._stub import stub_run

AGENT_NAME = "weather_wind"


async def run(state: AgentState) -> dict:
    return await stub_run(AGENT_NAME, "Weather & Wind analysis", state)
