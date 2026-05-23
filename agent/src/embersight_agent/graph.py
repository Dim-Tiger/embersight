"""LangGraph StateGraph wiring.

Topology (per ingest):

  orchestrator
      |
   (fan-out)
   /  |  |  \\
  W   T  V   R     # weather, terrain, values, routing - parallel
   \\ /    \\ /
   spread        # depends on Weather + Terrain
   /    \\
  RR    EI       # resource rec + evac intel - parallel, depend on Spread + Values
   \\   /
   master_ic     # synthesizes everything + interrupts for IC approval
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from .agents import (
    evacuation_intelligence,
    master_ic,
    resource_recommendation,
    routing_staging,
    spread_simulation,
    terrain_fuel,
    values_at_risk,
    weather_wind,
)
from .state import AgentState


async def orchestrator(state: AgentState) -> dict:
    """Ingest node. Pass-2 will resolve the incident from upstream APIs and
    pre-load whatever shared context the fan-out subagents need."""
    return {"scratch": {"started": True}}


def build_graph_definition() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("orchestrator", orchestrator)
    g.add_node("weather_wind", weather_wind.run)
    g.add_node("terrain_fuel", terrain_fuel.run)
    g.add_node("values_at_risk", values_at_risk.run)
    g.add_node("routing_staging", routing_staging.run)
    g.add_node("spread_simulation", spread_simulation.run)
    g.add_node("resource_recommendation", resource_recommendation.run)
    g.add_node("evacuation_intelligence", evacuation_intelligence.run)
    g.add_node("master_ic", master_ic.run)

    g.add_edge(START, "orchestrator")

    # Fan-out: 4 parallel branches off the orchestrator.
    for n in ("weather_wind", "terrain_fuel", "values_at_risk", "routing_staging"):
        g.add_edge("orchestrator", n)

    # Spread depends on Weather + Terrain (LangGraph waits for all in-edges).
    g.add_edge("weather_wind", "spread_simulation")
    g.add_edge("terrain_fuel", "spread_simulation")

    # Resource & Evac depend on Spread + Values.
    g.add_edge("spread_simulation", "resource_recommendation")
    g.add_edge("values_at_risk", "resource_recommendation")
    g.add_edge("spread_simulation", "evacuation_intelligence")
    g.add_edge("values_at_risk", "evacuation_intelligence")

    # Master IC waits for the action-producing branches and routing.
    g.add_edge("resource_recommendation", "master_ic")
    g.add_edge("evacuation_intelligence", "master_ic")
    g.add_edge("routing_staging", "master_ic")

    g.add_edge("master_ic", END)

    return g


def _checkpoint_db_path() -> str:
    return os.environ.get("EMBERSIGHT_CHECKPOINT_DB", "/tmp/embersight.db")


@asynccontextmanager
async def compiled_graph() -> AsyncIterator:
    """Yield a graph compiled with a SqliteSaver. Async context manager so
    the underlying connection is cleaned up cleanly."""
    db_path = _checkpoint_db_path()
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        yield build_graph_definition().compile(checkpointer=saver)
