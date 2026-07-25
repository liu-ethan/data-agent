import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database
from app.security.sql_sandbox import SandboxError


def _events(state):
    return list(iter_pipeline_events(state))


def _propose_sql(sql):
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "call-propose",
                "name": "propose_sql",
                "arguments": {"sql": sql},
            }
        ],
    }


def test_clarification_path_no_sql(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.7,
        "summary": "模糊渠道表现",
        "route_mode": "react",
        "slots": {"metrics": [], "time_range": None, "group_by": ["channel"]},
        "need_clarification": True,
        "clarification_question": "想按 GMV 还是订单量？时间用近 7 天还是 30 天？",
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ):
        events = _events({
            "question": "最近哪个渠道表现最好？",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_c",
            "trace_id": "req_c",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })
    names = [e for e, _ in events]
    assert "route_decision" in names
    assert any(
        e == "node_end"
        and d == {"node": "ComplexityRouter", "summary": "react"}
        for e, d in events
    )
    assert "sql" not in names
    assert "rows" not in names
    assert any(e == "answer" for e in names)
    done = next(d for e, d in events if e == "done")
    assert done["need_clarification"] is True


def test_happy_path_events(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "渠道 GMV",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "GROUP BY channel ORDER BY gmv DESC LIMIT 5"
    )
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.nodes.react_agent.chat_completion_with_tools",
        return_value=_propose_sql(sql),
    ), patch(
        "app.agent.answer_composer.compose_answer",
        return_value="渠道 A 领先",
    ):
        events = _events({
            "question": "上个月 GMV 最高的 5 个渠道是什么？",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_h",
            "trace_id": "req_h",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })
    by = {e: d for e, d in events}
    assert "run_start" in by
    assert by["route_decision"]["route_mode"] == "react"
    assert by["route_decision"]["route_source"] == "rule_override"
    assert "sql" in by
    assert "rows" in by
    assert by["answer"]["text"] == "渠道 A 领先"
    assert by["done"]["need_clarification"] is False
    names = [e for e, _ in events]
    assert "tool_start" in names
    assert "tool_end" in names
    assert any(
        e == "node_end"
        and d == {"node": "ComplexityRouter", "summary": "react"}
        for e, d in events
    )


def test_guardrail_rejection_emits_error_without_rows(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "删除订单",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "write_intent": True,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.nodes.react_agent.chat_completion_with_tools",
        return_value=_propose_sql("DELETE FROM orders"),
    ):
        events = _events({
            "question": "删除上个月的订单",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_guardrail_error",
            "trace_id": "req_guardrail_error",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })

    names = [event for event, _ in events]
    assert "error" in names
    assert "rows" not in names
    node_names = [
        data["node"] for event, data in events if event == "node_end"
    ]
    assert "SQLRepairer" not in node_names
    assert "MemorySave" in node_names
    guardrail_end = next(
        data
        for event, data in events
        if event == "node_end" and data["node"] == "SQLGuardrail"
    )
    assert guardrail_end["summary"] == "rejected"


def test_executor_failure_emits_error_and_skips_answer_composer(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "渠道 GMV",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "GROUP BY channel"
    )
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.nodes.react_agent.chat_completion_with_tools",
        return_value=_propose_sql(sql),
    ), patch(
        "app.tools.builtins.sandbox_execute",
        side_effect=SandboxError("database unavailable"),
    ), patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        return_value=(
            "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
            "GROUP BY channel"
        ),
    ):
        events = _events({
            "question": "上个月各渠道 GMV 是多少？",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_executor_error",
            "trace_id": "req_executor_error",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })

    names = [event for event, _ in events]
    assert ("error", {"message": "database unavailable"}) in events
    assert "rows" not in names
    assert not any(
        event == "node_end" and data["node"] == "AnswerComposer"
        for event, data in events
    )
    executor_end = next(
        data
        for event, data in events
        if event == "node_end" and data["node"] == "SQLExecutor"
    )
    assert executor_end["summary"] == "failed"


def test_repair_then_guardrail_and_success(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv",
        "route_mode": "coordinator",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "top_n": 5,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    bad_sql = "SELECT channel, SUM(pay_ammount) AS gmv FROM orders GROUP BY channel"
    good_sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "WHERE status = 'completed' GROUP BY channel LIMIT 5"
    )

    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json),
    ), patch(
        "app.agent.sql_generator.generate_sql",
        return_value=bad_sql,
    ), patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        return_value=good_sql,
    ), patch(
        "app.agent.answer_composer.compose_answer",
        return_value="ok",
    ):
        events = _events({
            "question": "对比各渠道 GMV Top5",
            "session_id": "s_repair",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_r",
            "trace_id": "req_r",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })

    names = [event for event, _ in events]
    assert "route_decision" in names
    assert "error" not in names
    sql_events = [data for event, data in events if event == "sql"]
    assert sum(bool(data.get("repaired")) for data in sql_events) == 1
    node_names = [
        data["node"] for event, data in events if event == "node_end"
    ]
    repair_index = node_names.index("SQLRepairer")
    assert node_names.index("SQLGuardrail", repair_index + 1) > repair_index
    assert node_names.index("SQLExecutor", repair_index + 1) > repair_index
    assert node_names.count("SQLRepairer") == 1
    assert "MemorySave" in node_names
    assert ("answer", {"text": "ok"}) in events
