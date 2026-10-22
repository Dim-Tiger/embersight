"""Citation helper: name the narrator that actually wrote the text.

Without an ANTHROPIC_API_KEY (or when the call fails) every agent writes its
narrative from a deterministic template. Citing "claude-haiku-4-5" in that
case would claim an LLM produced text it never saw.
"""

from __future__ import annotations

from ..state import Model

DETERMINISTIC = Model(name="deterministic template (no LLM call)", version="embersight")


def narrator_model(model_id: str, used_llm: bool) -> Model:
    return Model(name=model_id, version="anthropic") if used_llm else DETERMINISTIC.model_copy()
