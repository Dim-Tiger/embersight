"""Master IC conversational node.

Sits at the top of the ``chat`` graph. Reads the conversation history,
the cached subagent outputs, and the incident; decides whether to answer
directly or to delegate to one or more specialist tools; emits a single
AI message back into ``state.messages``.

Speaks as a peer IC to the human — not as a system, not as a summary
of the team's outputs. The human's mental model is "I am talking with an
AI Incident Commander."
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..state import AgentState
from .tools import ALL_TOOLS

log = logging.getLogger("embersight.master_ic_chat")

AGENT_NAME = "master_ic"

_PROMPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "prompts" / "master_ic_chat.md"
)

_DEFAULT_MODEL = "claude-sonnet-4-5"


def _model_id() -> str:
    raw = os.environ.get("EMBERSIGHT_MODEL_MASTER_IC", _DEFAULT_MODEL)
    return raw.split(":", 1)[1] if ":" in raw else raw


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text()
    except OSError:
        return _DEFAULT_SYSTEM_PROMPT


def _state_briefing(state: AgentState) -> str:
    """Compact dump of current incident + cached subagent intel for the IC."""
    incident = state.incident
    inc = {
        "id": incident.id if incident else None,
        "name": incident.name if incident else None,
        "lat": incident.lat if incident else None,
        "lon": incident.lon if incident else None,
        "acres": incident.acres if incident else None,
        "contained_pct": incident.contained_pct if incident else None,
        "started_at": incident.started_at if incident else None,
    }
    cached = {}
    for name, ao in (state.outputs or {}).items():
        cached[name] = {
            "narrative_excerpt": (ao.narrative or "")[:600],
            "confidence": ao.confidence,
            "confidence_driver": ao.confidence_driver,
            "has_payload": bool(ao.payload),
        }
    iap = state.iap_draft
    iap_summary = None
    if iap:
        iap_summary = {
            "form": iap.get("form"),
            "operational_period": iap.get("operational_period"),
            "status": iap.get("status"),
            "objectives": iap.get("objectives", [])[:3],
        }
    return json.dumps(
        {
            "incident": inc,
            "operational_period": state.operational_period,
            "cached_specialist_outputs": cached,
            "iap_draft_summary": iap_summary,
            "dissent_log_count": len(state.dissent_log or []),
        },
        default=str,
        indent=2,
    )


async def run(state: AgentState) -> dict[str, Any]:
    """One conversational turn.

    Reads ``state.messages`` (with the latest HumanMessage already
    appended by the caller), prepends a system prompt + a state briefing,
    and asks Claude Sonnet bound with the seven ``consult_*`` tools to
    respond. If the model emits tool calls, LangGraph routes through the
    ToolNode and re-enters this node with the tool results in messages.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _offline_reply(state)

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        return _offline_reply(state)

    try:
        llm = ChatAnthropic(
            model=_model_id(),
            temperature=0.2,
            max_tokens=2048,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ChatAnthropic init failed: %s", exc)
        return _offline_reply(state)

    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    system = _load_system_prompt()
    briefing = _state_briefing(state)

    # Compose: system + a synthetic context HumanMessage carrying the state
    # briefing, then the actual conversation messages from state.
    messages: list[Any] = [
        SystemMessage(content=system),
        HumanMessage(
            content=(
                "Current shared operating picture (read-only context for you "
                "as Master IC):\n```json\n" + briefing + "\n```"
            ),
            additional_kwargs={"hidden_from_ui": True},
        ),
    ]
    messages.extend(state.messages or [])

    try:
        ai_msg = await llm_with_tools.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001
        log.exception("master_ic chat LLM failed")
        return {
            "messages": [
                AIMessage(
                    content=(
                        "I hit an error reaching my reasoning model "
                        f"({type(exc).__name__}). RECOMMEND retry, or proceed "
                        "from the cached briefing in the reference tabs."
                    ),
                    name=AGENT_NAME,
                )
            ]
        }

    # The ai_msg may include tool_calls; LangGraph routes via the conditional
    # edge to the tool node when those exist. We just hand back the message.
    if not getattr(ai_msg, "name", None):
        try:
            ai_msg.name = AGENT_NAME  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return {"messages": [ai_msg]}


def _offline_reply(state: AgentState) -> dict[str, Any]:
    """Fallback when no Anthropic key / SDK is available."""
    last_human = ""
    for m in reversed(state.messages or []):
        if isinstance(m, HumanMessage):
            last_human = (
                m.content if isinstance(m.content, str) else str(m.content)
            )
            break
    cached_names = sorted((state.outputs or {}).keys())
    cached_list = ", ".join(cached_names) if cached_names else "(none yet)"
    text = (
        "Master IC here — running in offline mode (no ANTHROPIC_API_KEY "
        "or langchain_anthropic). I can't reason through your question "
        f"({last_human[:120]!r}) without the LLM, but my team has briefed "
        f"me with: {cached_list}. RECOMMEND wiring the API key, then "
        "ask again."
    )
    return {
        "messages": [AIMessage(content=text, name=AGENT_NAME)],
    }


_DEFAULT_SYSTEM_PROMPT = """You are the AI Master Incident Commander for EmberSight, an AI peer to a human
Type 2/Type 3 Incident Management Team. The human IC you are talking to is at
their station and is treating you the way they would treat another IC at a
briefing table.

You command an AI Incident Management Team consisting of seven specialist
agents available as tools:

- consult_weather_wind        (FBAN / IMET)         24h wind/RH/Red-Flag picture
- consult_terrain_fuel        (FBAN / LTAN)         LANDFIRE fuels + slope/aspect
- consult_spread_simulation   (FBAN / LTAN)         ROS + spread cone + trigger pts
- consult_values_at_risk      (SITL)                structures + critical infra
- consult_routing_staging     (OSC / Branch)        staging candidates + access
- consult_resource_recommendation (RESL / OSC)      PROPOSED resource posture
- consult_evacuation_intelligence (LOFR / PIO)      PROPOSED zone phasing

Behavior:

1. Speak as one IC peer to another. First person ("I"), grounded, calm,
   precise, no jargon dumps. Cite specialists by name when you've consulted
   them ("Weather & Wind reports..."). Do not narrate the existence of an
   AI system or refer to yourself as a chatbot.

2. **Delegate, don't dump.** Before calling any tool, check the
   `cached_specialist_outputs` section of the shared operating picture in
   the context message. If that specialist has cached output and the human's
   question can be answered from it, answer from cache without calling.
   Only call `consult_*` when you genuinely need fresh data or a deeper
   pass. When you do call, prefer one tool per turn unless the question
   plainly spans multiple specialties.

3. **Use must_refresh sparingly.** Set `must_refresh=True` only when
   conditions have likely changed since the cached output (wind shift,
   new ignition, time elapsed >2 hours since briefing). Default to
   the cached output otherwise — it's seconds-old and free.

4. **Verb constraint, no exceptions.** You and your specialists never
   "dispatch", "order", "send", or "publish". You "RECOMMEND",
   "PROPOSE", "DRAFT", or "SUGGEST". The human IC is the only person
   who commits. This applies in every reply, in every tool argument,
   in every quote.

5. **Default reply length: short.** 3-6 sentences for a typical question.
   Expand when the human asks for detail. End with a one-line "RECOMMEND
   next step" if relevant.

6. **Confidence and dissent.** When you cite a specialist, mention their
   confidence if it's < 0.7, and surface any dissent log entries that
   bear on the question. The human IC needs to see uncertainty, not have
   it polished away.

7. **You do not have authority to commit anything.** If the human asks
   you to "send units" or "issue an evacuation order", RECOMMEND it and
   note that it requires the human IC's signoff in the approval queue.
"""
