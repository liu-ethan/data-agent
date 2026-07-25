import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database


def _events(state):
    return list(iter_pipeline_events(state))


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
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.sql_generator.generate_sql",
        return_value=(
            "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
            "GROUP BY channel ORDER BY gmv DESC LIMIT 5"
        ),
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
    assert by["route_decision"]["route_source"] == "model"
    assert "sql" in by
    assert "rows" in by
    assert by["answer"]["text"] == "渠道 A 领先"
    assert by["done"]["need_clarification"] is False


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
        "app.agent.sql_generator.generate_sql",
        return_value="DELETE FROM orders",
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
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.sql_generator.generate_sql",
        return_value=(
            "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
            "GROUP BY channel"
        ),
    ), patch(
        "app.agent.nodes.sql_executor_node.execute_sql",
        side_effect=RuntimeError("database unavailable"),
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
