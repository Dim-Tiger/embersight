"""Specialist subagents.

Each module exposes an async `run(state: AgentState) -> dict` callable that
returns a state patch (`{"outputs": {agent_name: AgentOutput}}`).

Pass-1 implementations echo their role and return a stub AgentOutput so the
end-to-end stream and HITL flow can be exercised. Pass-2 will replace the
bodies with real tool calls.
"""
