"""Specialist subagents + Master IC nodes.

Each specialist module exposes an async `run(state: AgentState) -> dict`
callable returning a state patch (`{"outputs": {agent_name: AgentOutput}}`).

The Master IC has two personas in two modules:
- `master_ic.run` synthesizes an IAP draft at the end of a briefing graph run.
- `master_ic_chat.run` handles one conversational turn; binds the seven
  specialists as `consult_*` tools (defined in `agents/tools.py`).
"""
