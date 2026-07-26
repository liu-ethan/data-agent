from __future__ import annotations

import json

from app.agent.state import AgentState
from app.config import get_settings
from app.tools.builtins import ensure_builtins_registered
from app.tools.schemas import ToolContext

REACT_TOOL_NAMES = (
    "query_schema",
    "retrieve_metric_definition",
    "validate_sql",
    "propose_sql",
)

PROPOSE_SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
}

_FALLBACK_SCHEMAS = {
    "query_schema": {"type": "object", "properties": {}},
    "retrieve_metric_definition": {
        "type": "object",
        "properties": {"metric": {"type": "string"}},
        "required": ["metric"],
    },
    "validate_sql": PROPOSE_SQL_SCHEMA,
}


def build_react_openai_tools() -> list[dict]:
    registry = ensure_builtins_registered()
    specs = {spec.name: spec for spec in registry.list_tools()}
    tools = []
    for name in REACT_TOOL_NAMES[:-1]:
        spec = specs[name]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema or _FALLBACK_SCHEMAS[name],
                },
            }
        )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "propose_sql",
                "description": "Submit the final SQL query for guarded execution",
                "parameters": PROPOSE_SQL_SCHEMA,
            },
        }
    )
    return tools


def apply_propose_sql(arguments: dict) -> dict:
    sql = arguments.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("propose_sql requires a non-empty sql string")
    return {"generated_sql": sql.strip()}


def _tool_message(tool_call: dict, payload: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id") or ""),
        "name": str(tool_call.get("name") or ""),
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }


def react_tools_node(state: AgentState) -> dict:
    pending_calls = list(state.get("pending_tool_calls") or [])
    messages = list(state.get("react_messages") or [])
    tool_events: list[dict] = []
    out: dict = {
        "react_step": int(state.get("react_step") or 0) + 1,
        "pending_tool_calls": [],
    }
    registry = None
    context = None

    for tool_call in pending_calls:
        name = str(tool_call.get("name") or "")
        arguments = tool_call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        if name == "propose_sql":
            try:
                proposal = apply_propose_sql(arguments)
            except ValueError as exc:
                payload = {"ok": False, "error": str(exc)}
            else:
                out.update(proposal)
                payload = {"ok": True, **proposal}
        elif name in REACT_TOOL_NAMES:
            if registry is None:
                registry = ensure_builtins_registered()
                context = ToolContext(
                    request_id=state["request_id"],
                    trace_id=state["trace_id"],
                    session_id=state["session_id"],
                    user_id=state["user_id"],
                    user_role=state["user_role"],
                    node="ReActTools",
                )
            result = registry.invoke(name, arguments, context=context)
            tool_events.extend(result.events)
            payload = {
                "ok": result.ok,
                "data": result.data,
                "error": result.error,
            }
        else:
            payload = {"ok": False, "error": f"Tool is not allowed in ReAct: {name}"}

        messages.append(_tool_message(tool_call, payload))

    out["react_messages"] = messages
    out["tool_events"] = tool_events
    max_steps = get_settings().agent_react_max_steps
    if out["react_step"] >= max_steps and not out.get("generated_sql"):
        out["error"] = f"ReAct exceeded the maximum of {max_steps} tool steps"
    return out
