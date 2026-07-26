from __future__ import annotations

import json
import re

from app.agent.llm import chat_completion_with_tools
from app.agent.state import AgentState
from app.agent.nodes.react_tools import build_react_openai_tools
from app.config import get_settings
from app.prompts import render

_SQL_FENCE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)


def _initial_messages(state: AgentState) -> list[dict]:
    slots_json = json.dumps(state.get("slots") or {}, ensure_ascii=False)
    parts = render(
        "react_agent",
        slots_json=slots_json,
        question=state.get("question") or "",
    )
    return [
        {"role": "system", "content": parts["system"]},
        {"role": "user", "content": parts["user"]},
    ]


def _assistant_message(content: str | None, tool_calls: list[dict]) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call["id"],
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(
                        tool_call.get("arguments") or {}, ensure_ascii=False
                    ),
                },
            }
            for tool_call in tool_calls
        ]
    return message


def _extract_sql(content: str | None) -> str | None:
    raw = (content or "").strip()
    fenced = _SQL_FENCE.search(raw)
    candidate = fenced.group(1).strip() if fenced else raw
    if re.match(r"^(SELECT|WITH)\b", candidate, re.IGNORECASE):
        return candidate
    return None


def react_agent(state: AgentState) -> dict:
    max_steps = get_settings().agent_react_max_steps
    if int(state.get("react_step") or 0) >= max_steps:
        return {
            "error": f"ReAct exceeded the maximum of {max_steps} tool steps",
            "pending_tool_calls": [],
        }

    messages = list(state.get("react_messages") or _initial_messages(state))
    try:
        completion = chat_completion_with_tools(
            messages,
            build_react_openai_tools(),
        )
    except Exception as exc:
        return {
            "error": str(exc) or "ReAct LLM call failed",
            "pending_tool_calls": [],
        }

    tool_calls = list(completion.get("tool_calls") or [])
    content = completion.get("content")
    messages.append(_assistant_message(content, tool_calls))

    out: dict = {
        "react_messages": messages,
        "pending_tool_calls": tool_calls,
    }
    if not tool_calls and not state.get("generated_sql"):
        sql = _extract_sql(content)
        if sql:
            out["pending_tool_calls"] = [
                {
                    "id": "content-sql-propose",
                    "name": "propose_sql",
                    "arguments": {"sql": sql},
                }
            ]
    return out
