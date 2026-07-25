from __future__ import annotations

from app.agent.state import AgentState


def after_react_agent(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    if state.get("pending_tool_calls"):
        return "ReActTools"
    if state.get("generated_sql"):
        return "SQLGuardrail"
    return "MemorySave"


def after_react_tools(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    if state.get("generated_sql"):
        return "SQLGuardrail"
    if int(state.get("react_step") or 0) >= 5:
        return "MemorySave"
    return "ReActAgent"
