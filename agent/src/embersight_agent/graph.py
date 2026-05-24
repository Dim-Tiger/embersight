"""LangGraph wiring — two graphs sharing one checkpointed thread.

Both graphs operate on AgentState and persist through a SqliteSaver, so a
single thread_id can see an initial briefing run followed by an arbitrary
number of chat turns. State is shared: cached subagent outputs from the
briefing remain available to the IC during chat.

Briefing topology (one-shot, ~30-60s):

  orchestrator
      |
   (fan-out)
   /  |  |
  W   T  V              # weather, terrain, values - parallel
   \\ / \\ /
    R                    # routing depends on weather (wind) + terrain (DEM/slope)
   spread               # depends on Weather + Terrain
   /    \\
  RR    EI              # resource rec + evac intel - parallel, depend on Spread + Values
   \\   /
   master_ic            # synthesizes IAP + interrupts for IC approval

Chat topology (per user message, ~3-15s):

  master_ic_chat
       |
  (tool_calls?)
   yes |
       v
  tools (consult_*)  # writes back into outputs via Command
       |
       +─loops─> master_ic_chat (max 2 iterations)
       |
   no  v
      END
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .agents import (
    evacuation_intelligence,
    master_ic,
    master_ic_chat,
    resource_recommendation,
    routing_staging,
    spread_simulation,
    terrain_fuel,
    values_at_risk,
    weather_wind,
)
from .agents.tools import ALL_TOOLS
from .state import AgentState


# --------------------------------------------------------------------------- #
# Briefing graph
# --------------------------------------------------------------------------- #


async def orchestrator(state: AgentState) -> dict:
    """Briefing ingest node. Sets a scratch flag so downstream nodes can
    distinguish "we just kicked off" from "we're mid-chat-loop"."""
    return {"scratch": {"started": True, "mode": "briefing"}}


def build_briefing_graph() -> StateGraph:
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

    # weather/terrain/values fan out from the orchestrator; routing waits on
    # weather (wind direction → upwind-favoured staging + egress reranking)
    # AND terrain (real AOI mean elevation + slope feed the scoring axes).
    for n in ("weather_wind", "terrain_fuel", "values_at_risk"):
        g.add_edge("orchestrator", n)
    g.add_edge("weather_wind", "routing_staging")
    g.add_edge("terrain_fuel", "routing_staging")

    g.add_edge("weather_wind", "spread_simulation")
    g.add_edge("terrain_fuel", "spread_simulation")

    g.add_edge("spread_simulation", "resource_recommendation")
    g.add_edge("values_at_risk", "resource_recommendation")
    g.add_edge("spread_simulation", "evacuation_intelligence")
    g.add_edge("values_at_risk", "evacuation_intelligence")

    g.add_edge("resource_recommendation", "master_ic")
    g.add_edge("evacuation_intelligence", "master_ic")
    g.add_edge("routing_staging", "master_ic")

    g.add_edge("master_ic", END)
    return g


# --------------------------------------------------------------------------- #
# Chat graph
# --------------------------------------------------------------------------- #


def _route_after_chat(state: AgentState) -> Literal["tools", "end"]:
    """Conditional edge: if the last AIMessage has tool_calls, route into
    the ToolNode; otherwise terminate the turn."""
    messages = state.messages or []
    if not messages:
        return "end"
    last = messages[-1]
    # AIMessage with tool_calls -> route to tools
    tool_calls = getattr(last, "tool_calls", None)
    if tool_calls:
        return "tools"
    return "end"


def build_chat_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("master_ic_chat", master_ic_chat.run)
    g.add_node("tools", ToolNode(ALL_TOOLS))

    g.add_edge(START, "master_ic_chat")
    g.add_conditional_edges(
        "master_ic_chat",
        _route_after_chat,
        {"tools": "tools", "end": END},
    )
    # After tools, give the IC one more turn to summarize the tool results.
    g.add_edge("tools", "master_ic_chat")
    return g


# --------------------------------------------------------------------------- #
# Compiled graph context managers (share the same SqliteSaver)
# --------------------------------------------------------------------------- #


def _checkpoint_db_path() -> str:
    return os.environ.get("EMBERSIGHT_CHECKPOINT_DB", "/tmp/embersight.db")


@asynccontextmanager
async def compiled_graph(mode: Literal["briefing", "chat"] = "briefing") -> AsyncIterator:
    """Yield a compiled graph for the requested mode. Both modes share the
    same SqliteSaver so a single thread_id retains state across calls."""
    db_path = _checkpoint_db_path()
    async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
        builder = build_briefing_graph() if mode == "briefing" else build_chat_graph()
        yield builder.compile(checkpointer=saver)
