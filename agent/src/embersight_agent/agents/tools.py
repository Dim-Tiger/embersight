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
import logging
from typing import Annotated, Any

log = logging.getLogger("embersight.consult_tools")

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel

try:  # langgraph 1.0+
    from langchain_core.tools import InjectedToolCallId  # type: ignore
except ImportError:  # pragma: no cover -- older fallback
    from langgraph.prebuilt import InjectedToolCallId  # type: ignore

from ..hitl import request_human_decision
from ..state import AgentOutput, AgentState

try:  # langgraph 0.2+
    from langgraph.errors import GraphBubbleUp
except ImportError:  # pragma: no cover
    GraphBubbleUp = ()  # type: ignore[assignment]
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
    question: str | None = None,
) -> Command:
    # Plumb the IC's `question` through to the specialist as a directed
    # instruction parked in state.scratch.consult_instructions[agent_name].
    # Each specialist's run() reads its own slot and can branch on it
    # (e.g. evac_intel honors "test"/"demo"/"synthetic" by emitting
    # synthetic WARNING+ORDER proposals instead of operating on real
    # catalog data). This is the channel that lets the IC actually
    # direct its team rather than just poking specialists into rerunning
    # the same logic — without it, "tell evac_intel to make a test zone"
    # is impossible to express in the protocol.
    if question:
        instructions = dict(state.scratch.get("consult_instructions") or {})
        instructions[agent_name] = question
        state.scratch["consult_instructions"] = instructions

    # Visibility into what the consult tool actually sees. Logged at
    # WARNING so it surfaces under uvicorn's default INFO threshold for
    # third-party loggers. If this logs "cached_outputs=[]" while the
    # briefing clearly populated outputs, InjectedState isn't delivering
    # the merged checkpoint state — a known footgun on some langgraph
    # versions and the most likely explanation for the IC's recurring
    # "values_at_risk hasn't run yet" complaint mid-chat.
    log.warning(
        "[consult] consult_%s entered: incident=%s cached_outputs=%s "
        "must_refresh=%s question=%r",
        agent_name,
        state.incident.id if state.incident else None,
        sorted(state.outputs.keys()),
        must_refresh,
        (question or "")[:120],
    )

    # If we got a non-trivial instruction we always re-run the specialist,
    # even when a cached output exists. A cached output is the wrong answer
    # to a different question.
    has_instruction = bool(question and question.strip())
    effective_refresh = must_refresh or has_instruction

    cached = state.outputs.get(agent_name)
    if cached is not None and not effective_refresh:
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
    except GraphBubbleUp:
        # CRITICAL: GraphInterrupt (and any other GraphBubbleUp signal)
        # MUST propagate up to LangGraph so the graph pauses and the
        # interrupt envelope reaches the SSE stream as `interrupt_pending`.
        # The previous version's blanket `except Exception` was
        # converting the interrupt into a tool error string — the IC
        # would then paraphrase "I fired an interrupt!" while in fact
        # no checkpoint state was created, no interrupt_pending event
        # reached the frontend, and the approval queue stayed empty.
        # Symptom: human asks for a test proposal, IC narrates success,
        # nothing visible in the UI.
        raise
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
        "weather_wind", must_refresh, _resolve_state(state), tool_call_id, question
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
        "terrain_fuel", must_refresh, _resolve_state(state), tool_call_id, question
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
        "spread_simulation", must_refresh, _resolve_state(state), tool_call_id, question
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
        "values_at_risk", must_refresh, _resolve_state(state), tool_call_id, question
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
        "routing_staging", must_refresh, _resolve_state(state), tool_call_id, question
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
        "resource_recommendation", must_refresh, _resolve_state(state), tool_call_id, question
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

    The `question` argument is a directed instruction the specialist
    will read and act on, not just metadata. Use it to tell evac_intel
    what you actually want:
      - "default analysis"                            → standard catalog+cone pass
      - "this is a test, generate one WARNING and one ORDER synthetic
         proposal"                                     → fires two synthetic
                                                         interrupts (good for
                                                         demos / system checks)
      - "shrink the proposed ORDER zone 200m south"   → refinement (future)
    `must_refresh=True` is automatic whenever `question` is non-empty —
    a cached output is the wrong answer to a different question.
    """
    return await _consult_impl(
        "evacuation_intelligence",
        must_refresh,
        _resolve_state(state),
        tool_call_id,
        question,
    )


@tool
async def synthesize_test_evac_proposal(
    status: str,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """Create a SYNTHETIC evac_zone_change proposal for testing/demo.

    Use this when the human IC explicitly asks for a *test* or *demo*
    evacuation proposal (e.g. "make Evac Intel create one warning and
    one order zone for my system test"). It bypasses the normal
    catalog/cone pipeline and emits a single ``evac_zone_change``
    interrupt with a synthetic polygon centered near the incident, so
    the approval queue, map overlay, and Refine chat paths can be
    exercised end-to-end without waiting for real spread cones or live
    Genasys data.

    Parameters
    ----------
    status: "WARNING" or "ORDER" — the proposed status the IC should
        approve / reject. NORMAL is rejected (no point proposing a
        no-op).

    The tool fires the LangGraph ``interrupt()`` directly. The chat
    graph pauses; the human approves or rejects via the same
    /agent/resume endpoint the briefing path uses; on approve the
    polygon flows into ``acceptedEvacZones`` and renders solid on the
    map.

    Constraints carried over from the production path:
    - Verb is PROPOSED, not "issued" / "ordered".
    - The envelope includes WHY bullets explicitly marked as
      ``rationale_source="synthetic_test"`` so the IC card surfaces
      "DEMO" rather than implying real-data backing.
    """
    s = _resolve_state(state)
    proposed = (status or "").strip().upper()
    if proposed not in {"WARNING", "ORDER"}:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "status": "error",
                                "error": (
                                    "synthesize_test_evac_proposal requires "
                                    "status in {'WARNING', 'ORDER'}; got "
                                    f"{status!r}"
                                ),
                            }
                        ),
                        tool_call_id=tool_call_id,
                        name="synthesize_test_evac_proposal",
                    )
                ]
            }
        )

    inc = s.incident
    if inc is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "status": "error",
                                "error": "no incident in state — synthesize requires a selected fire.",
                            }
                        ),
                        tool_call_id=tool_call_id,
                        name="synthesize_test_evac_proposal",
                    )
                ]
            }
        )

    # Synthetic polygon: a ~2 km square offset NE of the incident for
    # ORDER (closer, hotter) or NW for WARNING. Tiny so the demo polygon
    # is visually distinct from real catalog zones.
    half = 0.012  # ~1.3 km at CA latitudes
    if proposed == "ORDER":
        cx, cy = inc.lon + 0.015, inc.lat + 0.012
        zid = f"TEST-ORDER-{int(__import__('time').time())}"
        zname = f"Test Order Zone (synthetic, near {inc.name})"
        population = 1850
        why = [
            "Synthetic test proposal — IC requested a demo ORDER zone.",
            "Polygon placed ~1.5 km NE of incident centroid for visual clarity.",
            "Egress modeled as at-risk to exercise the impact card.",
        ]
        impact = {
            "human_displacement_estimate": population,
            "residential_structures_estimate": 740,
            "egress_clear": False,
            "egress_blocked_edges": 2,
        }
    else:  # WARNING
        cx, cy = inc.lon - 0.018, inc.lat + 0.014
        zid = f"TEST-WARN-{int(__import__('time').time())}"
        zname = f"Test Warning Zone (synthetic, near {inc.name})"
        population = 920
        why = [
            "Synthetic test proposal — IC requested a demo WARNING zone.",
            "Polygon placed ~1.8 km NW of incident centroid for visual clarity.",
            "Egress modeled as clear to exercise the impact card.",
        ]
        impact = {
            "human_displacement_estimate": population,
            "residential_structures_estimate": 365,
            "egress_clear": True,
            "egress_blocked_edges": 0,
        }

    polygon_geojson = {
        "type": "Polygon",
        "coordinates": [
            [
                [cx - half, cy - half],
                [cx + half, cy - half],
                [cx + half, cy + half],
                [cx - half, cy + half],
                [cx - half, cy - half],
            ]
        ],
    }

    envelope = {
        "type": "evac_zone_change",
        "zone_id": zid,
        "name": zname,
        "jurisdiction": "Synthetic (test)",
        "current_status": "NORMAL",
        "proposed_status": proposed,
        "rationale": why[0],
        "rationale_source": "synthetic_test",
        "why": why,
        "impact": impact,
        "polygon_geojson": polygon_geojson,
        "population_estimate": population,
    }

    # Fire the interrupt and wait for the human's decision. The chat
    # graph pauses here; the IC's next-turn synthesis will include the
    # decision once /agent/resume runs.
    decision = request_human_decision("evac_zone_change", envelope)

    summary = {
        "status": "ok",
        "proposed_status": proposed,
        "zone_id": zid,
        "decision": decision,
        "note": (
            "Synthetic test proposal fired as an evac_zone_change "
            "interrupt. The IC approves or rejects via the same "
            "approval queue used for real proposals."
        ),
    }
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(summary, default=str),
                    tool_call_id=tool_call_id,
                    name="synthesize_test_evac_proposal",
                )
            ]
        }
    )


ALL_TOOLS = [
    consult_weather_wind,
    consult_terrain_fuel,
    consult_spread_simulation,
    consult_values_at_risk,
    consult_routing_staging,
    consult_resource_recommendation,
    consult_evacuation_intelligence,
    # synthesize_test_evac_proposal was a separate tool that did the
    # same thing as consult_evacuation_intelligence(question="test mode...").
    # Kept defined above as a fallback but no longer exposed to the IC —
    # the instruction-passthrough channel is the canonical way to direct
    # specialists.
]
