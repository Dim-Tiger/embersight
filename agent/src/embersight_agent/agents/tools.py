"""Subagent tools exposed to the Master IC in chat mode.

Each tool wraps a specialist subagent's ``run(state)`` so the Master IC can
either return the cached output (fast, <50 ms) or re-invoke the specialist
against the live incident (slow, 5-30 s). Tool invocations write back into
``state.outputs`` via a Command, so the dashboard reference tabs reflect
the freshest data the moment a specialist is re-consulted.

The Master IC selects tools via LangGraph's tool-node loop; the human never
calls these directly.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel

try:  # langgraph 1.0+
    from langchain_core.tools import InjectedToolCallId  # type: ignore
except ImportError:  # pragma: no cover -- older fallback
    from langgraph.prebuilt import InjectedToolCallId  # type: ignore

from ..state import AgentOutput, AgentState
from . import (
    evacuation_intelligence,
    resource_recommendation,
    routing_staging,
    spread_simulation,
    terrain_fuel,
    values_at_risk,
    weather_wind,
)

_SPECIALISTS = {
    "weather_wind": weather_wind,
    "terrain_fuel": terrain_fuel,
    "spread_simulation": spread_simulation,
    "values_at_risk": values_at_risk,
    "routing_staging": routing_staging,
    "resource_recommendation": resource_recommendation,
    "evacuation_intelligence": evacuation_intelligence,
}


# --------------------------------------------------------------------------- #
# Summary helpers
# --------------------------------------------------------------------------- #


_HEADLINE_KEYS = [
    "red_flag",
    "critical_window",
    "rollup",
    "candidates",
    "high_risk_zones",
    "head_ros_chains_per_hr",
    "flame_length_ft",
    "zones_advisory",
    "zones_order",
    "key_findings",
    "primary_routes",
    "egress_routes",
]


def _summarize(output: AgentOutput | None, agent_name: str) -> dict[str, Any]:
    """Tight summary the Master IC can fold into its next reply.

    Returns narrative + confidence + a handful of headline payload fields.
    The full payload stays on AgentState.outputs for the reference tabs.
    """
    if output is None:
        return {
            "agent": agent_name,
            "status": "no_output",
            "note": (
                f"{agent_name} has no cached output yet. Re-run with "
                "must_refresh=True to fetch fresh data."
            ),
        }
    payload = output.payload or {}
    headlines = {k: payload[k] for k in _HEADLINE_KEYS if k in payload}
    citations = []
    if output.citation_bundle and output.citation_bundle.datasets:
        for d in output.citation_bundle.datasets[:5]:
            citations.append({"name": d.name, "url": d.url})
    return {
        "agent": agent_name,
        "narrative": output.narrative,
        "confidence": output.confidence,
        "confidence_driver": output.confidence_driver,
        "headlines": headlines,
        "citations": citations,
    }


def _resolve_state(state: AgentState | dict[str, Any] | None) -> AgentState:
    """InjectedState may hand us either a typed AgentState or a dict; coerce."""
    if state is None:
        return AgentState()
    if isinstance(state, AgentState):
        return state
    if isinstance(state, BaseModel):
        return AgentState.model_validate(state.model_dump())
    return AgentState.model_validate(state)


async def _consult_impl(
    agent_name: str,
    must_refresh: bool,
    state: AgentState,
    tool_call_id: str,
) -> Command:
    cached = state.outputs.get(agent_name)
    if cached is not None and not must_refresh:
        summary = _summarize(cached, agent_name)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(summary, default=str),
                        tool_call_id=tool_call_id,
                        name=f"consult_{agent_name}",
                    )
                ]
            }
        )

    module = _SPECIALISTS[agent_name]
    try:
        patch = await module.run(state)
    except Exception as exc:  # noqa: BLE001 -- never propagate to the IC
        err = {
            "agent": agent_name,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(err),
                        tool_call_id=tool_call_id,
                        name=f"consult_{agent_name}",
                    )
                ]
            }
        )

    new_outputs = patch.get("outputs") or {}
    new_output = new_outputs.get(agent_name)
    summary = _summarize(new_output, agent_name)
    return Command(
        update={
            "outputs": new_outputs,  # merged via _merge_outputs reducer
            "messages": [
                ToolMessage(
                    content=json.dumps(summary, default=str),
                    tool_call_id=tool_call_id,
                    name=f"consult_{agent_name}",
                )
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Tool definitions. Docstrings are the model-facing spec.
# --------------------------------------------------------------------------- #


@tool
async def consult_weather_wind(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Weather & Wind specialist (FBAN / IMET).

    Covers: 24-hour wind & RH trend, Red Flag / Fire Weather Watch status,
    HRRR vs RTMA model agreement, nearby RAWS observations, worst-case
    fire-weather hour. Defaults to cached output from the initial briefing;
    set must_refresh=True if wind has materially shifted or RH crossover
    is suspected.
    """
    return await _consult_impl(
        "weather_wind", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_terrain_fuel(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Terrain & Fuel specialist (FBAN / LTAN).

    Covers: LANDFIRE FBFM40 dominant fuel models, slope / aspect / elevation
    statistics over the AOI, canopy structure. Refresh only if the AOI
    expanded or fuel conditions changed dramatically.
    """
    return await _consult_impl(
        "terrain_fuel", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_spread_simulation(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Spread Simulation specialist (FBAN / LTAN).

    Covers: head rate of spread, flame length, predicted 12 / 24 hour
    spread cone, high-risk zones, trigger-point breaches. Refresh whenever
    wind shifts >30 degrees, RH drops below 15 percent, or fire behavior
    surprises the team.
    """
    return await _consult_impl(
        "spread_simulation", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_values_at_risk(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Values-at-Risk specialist (SITL).

    Covers: structures in cone (residential / commercial / industrial),
    hospitals / schools, transmission lines, critical facilities. Refresh
    only when the spread cone changes materially.
    """
    return await _consult_impl(
        "values_at_risk", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_routing_staging(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Routing & Staging specialist (OSC / Branch Director).

    Covers: candidate staging areas (score, distance to fire, water access,
    surface), OSM road graph, primary ingress / egress routes. Refresh if
    staging access changes (road closure, new ignition near staging).
    """
    return await _consult_impl(
        "routing_staging", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_resource_recommendation(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Resource Recommendation specialist (RESL / OSC).

    Covers: PROPOSED engines / crews / dozers / air tankers / helicopters
    and rationale. ALL output is RECOMMENDED — EmberSight never dispatches.
    Refresh when posture clearly needs to escalate or de-escalate.
    """
    return await _consult_impl(
        "resource_recommendation", must_refresh, _resolve_state(state), tool_call_id
    )


@tool
async def consult_evacuation_intelligence(
    question: str,
    must_refresh: bool = False,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Consult the Evacuation Intelligence specialist (LOFR / PIO).

    Covers: PROPOSED evacuation order / warning / watch zones (Cal OES
    Zonehaven schema), egress route status. ALL output is PROPOSED.
    Refresh when the spread cone or wind direction shifts.
    """
    return await _consult_impl(
        "evacuation_intelligence",
        must_refresh,
        _resolve_state(state),
        tool_call_id,
    )


ALL_TOOLS = [
    consult_weather_wind,
    consult_terrain_fuel,
    consult_spread_simulation,
    consult_values_at_risk,
    consult_routing_staging,
    consult_resource_recommendation,
    consult_evacuation_intelligence,
]
