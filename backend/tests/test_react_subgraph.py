import json
from unittest.mock import Mock

import pytest

from app.db.init_db import init_database
from app.agent.nodes.react_agent import react_agent
from app.agent.nodes.react_tools import (
    REACT_TOOL_NAMES,
    apply_propose_sql,
    build_react_openai_tools,
    react_tools_node,
)


def test_react_tools_exclude_execute_sql():
    names = {tool["function"]["name"] for tool in build_react_openai_tools()}
    assert "execute_sql" not in names
    assert "render_chart" not in names
    assert set(REACT_TOOL_NAMES) == names


def test_propose_sql_writes_state():
    out = apply_propose_sql({"sql": "SELECT 1"})
    assert out["generated_sql"] == "SELECT 1"


@pytest.mark.parametrize("sql", ["", "   ", None])
def test_propose_sql_rejects_empty_sql(sql):
    with pytest.raises(ValueError, match="non-empty"):
        apply_propose_sql({"sql": sql})


def test_react_tools_node_applies_proposal_and_clears_calls():
    state = {
        "react_messages": [],
        "react_step": 1,
        "pending_tool_calls": [
            {
                "id": "call-1",
                "name": "propose_sql",
                "arguments": {"sql": " SELECT 1 "},
            }
        ],
    }

    out = react_tools_node(state)

    assert out["generated_sql"] == "SELECT 1"
    assert out["react_step"] == 2
    assert out["pending_tool_calls"] == []
    assert out["react_messages"][0]["role"] == "tool"
    assert out["react_messages"][0]["tool_call_id"] == "call-1"


def test_react_agent_initializes_messages_and_stores_tool_calls(monkeypatch):
    completion = Mock(
        return_value={
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "propose_sql",
                    "arguments": {"sql": "SELECT 1"},
                }
            ],
        }
    )
    monkeypatch.setattr(
        "app.agent.nodes.react_agent.chat_completion_with_tools", completion
    )

    out = react_agent(
        {
            "question": "How many orders?",
            "slots": {"metric": "order_count"},
            "react_step": 0,
        }
    )

    assert [message["role"] for message in out["react_messages"]] == [
        "system",
        "user",
        "assistant",
    ]
    assert out["pending_tool_calls"][0]["name"] == "propose_sql"
    completion.assert_called_once()


def test_react_agent_content_sql_synthesizes_propose_sql(monkeypatch):
    completion = Mock(
        return_value={
            "content": "```sql\nSELECT COUNT(*) FROM orders\n```",
            "tool_calls": [],
        }
    )
    monkeypatch.setattr(
        "app.agent.nodes.react_agent.chat_completion_with_tools", completion
    )

    out = react_agent({"question": "How many orders?", "react_step": 0})

    assert "generated_sql" not in out
    assert len(out["pending_tool_calls"]) == 1
    call = out["pending_tool_calls"][0]
    assert call["name"] == "propose_sql"
    assert call["arguments"]["sql"] == "SELECT COUNT(*) FROM orders"

    tools_out = react_tools_node(
        {
            "react_messages": out["react_messages"],
            "react_step": 0,
            "pending_tool_calls": out["pending_tool_calls"],
        }
    )
    assert tools_out["generated_sql"] == "SELECT COUNT(*) FROM orders"


def test_react_agent_stops_before_sixth_llm_call(monkeypatch):
    completion = Mock()
    monkeypatch.setattr(
        "app.agent.nodes.react_agent.chat_completion_with_tools", completion
    )

    out = react_agent({"question": "How many orders?", "react_step": 5})

    assert out["error"] == "ReAct exceeded the maximum of 5 tool steps"
    assert out["pending_tool_calls"] == []
    completion.assert_not_called()


def test_react_agent_llm_failure_sets_error_without_crash(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(
        "app.agent.nodes.react_agent.chat_completion_with_tools", boom
    )

    out = react_agent({"question": "How many orders?", "react_step": 0})

    assert out["error"] == "LLM unavailable"
    assert out["pending_tool_calls"] == []
    assert "react_messages" not in out


def _react_tool_state(**overrides):
    base = {
        "request_id": "req_react",
        "trace_id": "req_react",
        "session_id": "s_react",
        "user_id": "1",
        "user_role": "analyst",
        "react_messages": [],
        "react_step": 0,
        "pending_tool_calls": [],
    }
    base.update(overrides)
    return base


def test_react_tools_node_invokes_registry_tool(tmp_db_path):
    init_database(reset=True)
    state = _react_tool_state(
        pending_tool_calls=[
            {"id": "call-schema", "name": "query_schema", "arguments": {}}
        ],
    )

    out = react_tools_node(state)

    assert out["tool_events"]
    assert out["react_step"] == 1
    payload = json.loads(out["react_messages"][0]["content"])
    assert payload["ok"] is True
    assert "tables" in (payload.get("data") or {})


def test_react_tools_node_denies_execute_sql_without_executing(tmp_db_path):
    init_database(reset=True)
    state = _react_tool_state(
        pending_tool_calls=[
            {
                "id": "call-exec",
                "name": "execute_sql",
                "arguments": {"sql": "SELECT 1"},
            }
        ],
    )

    out = react_tools_node(state)

    assert out["tool_events"] == []
    payload = json.loads(out["react_messages"][0]["content"])
    assert payload["ok"] is False
    assert "not allowed" in payload["error"].lower()
    assert "execute_sql" in payload["error"]


def test_react_tools_node_sets_error_at_step_limit_without_sql(tmp_db_path):
    init_database(reset=True)
    state = _react_tool_state(
        react_step=4,
        pending_tool_calls=[
            {"id": "call-schema", "name": "query_schema", "arguments": {}}
        ],
    )

    out = react_tools_node(state)

    assert out["react_step"] == 5
    assert out["error"] == "ReAct exceeded the maximum of 5 tool steps"
    assert "generated_sql" not in out
