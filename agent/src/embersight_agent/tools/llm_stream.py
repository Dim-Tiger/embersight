"""Helper for streaming Anthropic chat-model calls inside LangGraph nodes.

Every subagent narrates its result with a Claude call. Historically each
agent called `await llm.ainvoke(messages)` which returns the whole
response at once — LangGraph then emits `on_chat_model_end` but no
`on_chat_model_stream` events, so the dashboard has nothing to surface
while the model is "thinking".

`stream_text(llm, messages)` switches to `astream`, which yields tokens
as they arrive. Each chunk fires an `on_chat_model_stream` event tagged
with the current `langgraph_node` (LangGraph propagates the running
node automatically through context). The frontend uses that attribution
to render live "thinking" text under the agent that owns the call.

Returns the concatenated text exactly like `ainvoke` did.
"""

from __future__ import annotations

from typing import Any, Iterable


async def stream_text(llm: Any, messages: Iterable[Any]) -> str:
    """Run `llm.astream(messages)` and return the concatenated text content.

    Falls back to `ainvoke` if the model class doesn't expose `astream`
    (some test doubles don't).
    """
    if not hasattr(llm, "astream"):
        resp = await llm.ainvoke(list(messages))
        return _coerce_to_text(getattr(resp, "content", resp))

    parts: list[str] = []
    async for chunk in llm.astream(list(messages)):
        parts.append(_coerce_to_text(getattr(chunk, "content", chunk)))
    return "".join(parts).strip()


def _coerce_to_text(content: Any) -> str:
    """Anthropic chunks can be plain str, a list of typed content blocks
    (`{"type":"text","text":"..."}`), or already a partial. Reduce them
    all to plain text without dropping anything visible to the user."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    out.append(str(block.get("text", "")))
                elif t == "thinking":
                    # Surface thinking blocks too; the UI marks them
                    # separately via the on_chat_model_stream metadata
                    # but the text itself is still useful prose.
                    out.append(str(block.get("thinking", "")))
        return "".join(out)
    return str(content)
