import json
from unittest.mock import patch

import pytest

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database
from app.security.sql_sandbox import SandboxError, sandbox_execute


def _state(request_id: str, question: str) -> dict:
    return {
        "question": question,
        "session_id": f"s_{request_id}",
        "user_id": "1",
        "user_role": "analyst",
        "request_id": request_id,
        "trace_id": request_id,
        "need_clarification": False,
        "repaired": False,
        "agent_trace": [],
    }


def test_react_route_uses_propose_sql_then_tail(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "top_n": 5,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "WHERE status = 'completed' GROUP BY channel LIMIT 5"
    )

    def fake_tools(messages, tools, temperature=0):
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "name": "propose_sql",
                    "arguments": {"sql": sql},
                }
            ],
        }

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.nodes.react_agent.chat_completion_with_tools",
            side_effect=fake_tools,
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="渠道汇总完成",
        ),
    ):
        events = list(
            iter_pipeline_events(
                _state("req_react", "各渠道 GMV Top5")
            )
        )

    payload = next(data for event, data in events if event == "route_decision")
    assert payload["route_mode"] == "react"
    node_names = [
        data["node"] for event, data in events if event == "node_end"
    ]
    assert "ReActAgent" in node_names
    assert "ReActTools" in node_names
    assert "SchemaRetriever" not in node_names
    assert ("sql", {"sql": sql, "repaired": False}) in events
    assert any(event == "rows" for event, _ in events)
    assert any(event == "answer" for event, _ in events)


def test_react_content_sql_emits_sql_sse_without_tool_calls(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "order count",
        "route_mode": "react",
        "slots": {"metrics": ["order_count"]},
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = "SELECT COUNT(*) AS n FROM orders"

    def fake_tools(messages, tools, temperature=0):
        return {
            "content": f"```sql\n{sql}\n```",
            "tool_calls": [],
        }

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.nodes.react_agent.chat_completion_with_tools",
            side_effect=fake_tools,
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="订单数已统计",
        ),
    ):
        events = list(
            iter_pipeline_events(_state("req_react_content_sql", "有多少订单"))
        )

    assert ("sql", {"sql": sql, "repaired": False}) in events
    node_names = [
        data["node"] for event, data in events if event == "node_end"
    ]
    assert "ReActTools" in node_names


def test_coordinator_keyword_override(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv comparison",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "GROUP BY channel"
    )

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch("app.agent.sql_generator.generate_sql", return_value=sql),
        patch("app.agent.answer_composer.compose_answer", return_value="ok"),
    ):
        events = list(
            iter_pipeline_events(
                _state("req_coordinator", "对比各渠道 GMV")
            )
        )

    payload = next(data for event, data in events if event == "route_decision")
    assert payload == {
        "route_mode": "coordinator",
        "route_source": "rule_override",
    }
    node_names = [
        data["node"] for event, data in events if event == "node_end"
    ]
    assert "SchemaRetriever" in node_names
    assert "ReActAgent" not in node_names


@pytest.mark.parametrize(
    ("bad_sql", "good_sql"),
    [
        (
            "SELECT channel, SUM(pay_ammount) AS gmv "
            "FROM orders GROUP BY channel",
            "SELECT channel, SUM(pay_amount) AS gmv "
            "FROM orders GROUP BY channel",
        ),
        (
            "SELECT channel, SUM(pay_amount) AS gmv FROM orders",
            "SELECT channel, SUM(pay_amount) AS gmv "
            "FROM orders GROUP BY channel",
        ),
        (
            "SELECT channel, SUM(pay_amount) AS gmv "
            "FROM orderz GROUP BY channel",
            "SELECT channel, SUM(pay_amount) AS gmv "
            "FROM orders GROUP BY channel",
        ),
    ],
    ids=["unknown-column", "missing-group-by", "wrong-table"],
)
def test_coordinator_repairs_invalid_sql(tmp_db_path, bad_sql, good_sql):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "channel gmv comparison",
        "route_mode": "coordinator",
        "slots": {
            "metrics": ["gmv", "order_count"],
            "group_by": ["channel"],
        },
        "need_clarification": False,
        "clarification_question": None,
    }

    def execute_sql(sql, *, user_role):
        # SQLite permits mixed aggregate/non-aggregate SELECT lists without
        # GROUP BY, so emulate the stricter production SQL dialect for this case.
        if sql == bad_sql and "GROUP BY" not in sql:
            raise SandboxError("missing GROUP BY")
        return sandbox_execute(sql, user_role=user_role)

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch("app.agent.sql_generator.generate_sql", return_value=bad_sql),
        patch(
            "app.agent.nodes.sql_repairer.chat_completion",
            return_value=good_sql,
        ),
        patch(
            "app.tools.builtins.sandbox_execute",
            side_effect=execute_sql,
        ),
        patch("app.agent.answer_composer.compose_answer", return_value="ok"),
    ):
        events = list(
            iter_pipeline_events(
                _state("req_repair_cases", "对比各渠道 GMV 和订单量")
            )
        )

    route = next(data for event, data in events if event == "route_decision")
    assert route["route_mode"] == "coordinator"
    node_starts = [
        data["node"] for event, data in events if event == "node_start"
    ]
    assert "SQLRepairer" in node_starts
    assert not any(event == "error" for event, _ in events)
    assert any(event in {"rows", "answer"} for event, _ in events)


def test_coordinator_guardrail_rejection_skips_repair(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "user_analysis",
        "confidence": 0.9,
        "summary": "user phone analysis",
        "route_mode": "coordinator",
        "slots": {
            "metrics": ["gmv", "order_count"],
            "group_by": ["phone"],
        },
        "need_clarification": False,
        "clarification_question": None,
    }

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.sql_generator.generate_sql",
            return_value="SELECT phone FROM users",
        ),
        patch(
            "app.agent.nodes.sql_repairer.chat_completion"
        ) as repair_completion,
    ):
        events = list(
            iter_pipeline_events(
                _state("req_sensitive_guardrail", "对比用户 GMV 和订单量")
            )
        )

    errors = [data for event, data in events if event == "error"]
    assert errors
    assert errors[0].get("trace_id") == "req_sensitive_guardrail"
    assert errors[0].get("request_id") == "req_sensitive_guardrail"
    assert not any(event == "rows" for event, _ in events)
    assert not any(
        event == "node_start" and data["node"] == "SQLRepairer"
        for event, data in events
    )
    repair_completion.assert_not_called()


def test_followup_inherits_slots(tmp_db_path):
    from app.agent.memory import store

    init_database(reset=True)
    store.ensure_session("s_fu", "1")
    store.save_turn(
        session_id="s_fu",
        user_id="1",
        question="最近30天各渠道GMV",
        intent="channel_analysis",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        result_summary="ok",
    )
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.8,
        "summary": "按城市拆",
        "route_mode": "react",
        "slots": {
            "metrics": [],
            "time_range": None,
            "group_by": ["city"],
            "top_n": None,
            "filters": None,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT city, SUM(pay_amount) AS gmv FROM orders "
        "WHERE status = 'completed' GROUP BY city LIMIT 100"
    )
    captured = {}

    def capture_intent(messages, temperature=0):
        captured["messages"] = messages
        return json.dumps(intent_json)

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            side_effect=capture_intent,
        ),
        patch(
            "app.agent.nodes.react_agent.chat_completion_with_tools",
            return_value={
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "name": "propose_sql",
                        "arguments": {"sql": sql},
                    }
                ],
            },
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="按城市拆解完成",
        ),
    ):
        events = list(
            iter_pipeline_events(
                {
                    "question": "那按城市拆一下",
                    "session_id": "s_fu",
                    "user_id": "1",
                    "user_role": "analyst",
                    "request_id": "req_fu",
                    "trace_id": "req_fu",
                    "need_clarification": False,
                    "repaired": False,
                    "agent_trace": [],
                }
            )
        )

    user_content = captured["messages"][-1]["content"]
    assert "gmv" in user_content
    assert "last_30d" in user_content
    done = next(data for event, data in events if event == "done")
    assert done["need_clarification"] is False
    assert any(event == "rows" for event, _ in events)

    saved = store.load_last_turn_slots("s_fu", "1")
    assert saved is not None
    assert saved["metrics"] == ["gmv"]
    assert saved["time_range"] == "last_30d"
    assert "city" in saved["group_by"]


def test_preferences_are_visible_in_new_session(tmp_db_path):
    from app.agent.memory import store
    from app.agent.nodes.memory_load import memory_load

    init_database(reset=True)
    store.update_preferences_from_slots(
        "1",
        {"time_range": "last_30d", "group_by": ["channel"]},
    )

    loaded = memory_load(
        {"session_id": "s_preferences_new", "user_id": "1"}
    )

    assert loaded["session_slots"] is None
    assert loaded["user_preferences"]["default_time_range"] == "last_30d"
    assert "channel" in loaded["user_preferences"]["preferred_dimensions"]


def test_memory_load_ownership_conflict_short_circuits(tmp_db_path):
    from app.agent.memory import store

    init_database(reset=True)
    store.ensure_session("s_owned", "1")

    with patch("app.agent.nodes.intent_analyzer.chat_completion") as intent_llm:
        events = list(
            iter_pipeline_events(
                {
                    "question": "各渠道 GMV",
                    "session_id": "s_owned",
                    "user_id": "2",
                    "user_role": "analyst",
                    "request_id": "req_own",
                    "trace_id": "req_own",
                    "need_clarification": False,
                    "repaired": False,
                    "agent_trace": [],
                }
            )
        )

    intent_llm.assert_not_called()
    node_names = [d["node"] for e, d in events if e == "node_start"]
    assert "MemoryLoad" in node_names
    assert "MemorySave" in node_names
    assert "IntentAnalyzer" not in node_names
    assert any(e == "error" for e, _ in events)
    assert "sql" not in [e for e, _ in events]
    assert "rows" not in [e for e, _ in events]
