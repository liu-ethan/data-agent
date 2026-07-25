import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database
from app.tools.schemas import ToolResult


def _state(request_id: str, question: str, **extra):
    base = {
        "question": question,
        "session_id": "default",
        "user_id": "1",
        "user_role": "analyst",
        "request_id": request_id,
        "trace_id": request_id,
        "repaired": False,
        "agent_trace": [],
    }
    base.update(extra)
    return base


def test_read_path_emits_chart(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv by channel",
        "route_mode": "coordinator",
        "slots": {"metrics": ["gmv"], "group_by": ["channel"], "top_n": 5},
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel AS channel, SUM(pay_amount) AS gmv "
        "FROM orders GROUP BY channel ORDER BY gmv DESC LIMIT 5"
    )
    chart = {
        "type": "bar",
        "x": "channel",
        "y": "gmv",
        "title": "渠道 GMV",
    }
    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.sql_generator.generate_sql",
            return_value=sql,
        ),
        patch(
            "app.agent.chart_planner.chat_completion",
            return_value=json.dumps(chart),
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="ok",
        ),
    ):
        events = list(
            iter_pipeline_events(_state("req_p6_chart", "各渠道 GMV Top5"))
        )

    assert any(e == "chart" for e, _ in events)
    chart_data = next(d for e, d in events if e == "chart")
    assert chart_data["type"] == "bar"
    assert "ChartPlanner" in [d["node"] for e, d in events if e == "node_end"]
    done = next(d for e, d in events if e == "done")
    assert "repaired" in done
    assert done["repaired"] is False


def _fake_invoke(name, args, context=None):
    if name == "validate_sql":
        return ToolResult(ok=True, data={"ok": True}, events=[])
    if name == "execute_sql":
        return ToolResult(
            ok=True,
            data={
                "affected_rows": 3,
                "is_write": True,
                "risk_level": "high",
            },
            events=[
                {
                    "event": "tool_start",
                    "data": {"tool": "execute_sql", "risk_level": "high"},
                },
                {
                    "event": "tool_end",
                    "data": {
                        "tool": "execute_sql",
                        "status": "ok",
                        "risk_level": "high",
                    },
                },
            ],
        )
    raise AssertionError(name)


def test_write_path_emits_write_result_no_chart(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "write_op",
        "confidence": 0.9,
        "summary": "update budget",
        "route_mode": "coordinator",
        "slots": {"write_intent": True},
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = "UPDATE campaigns SET budget = budget WHERE campaign_id = 1"
    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.sql_generator.generate_sql",
            return_value=sql,
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="写操作完成",
        ),
        patch(
            "app.agent.chart_planner.chat_completion"
        ) as chart_llm,
        patch(
            "app.tools.builtins.ensure_builtins_registered"
        ) as reg_factory,
    ):
        reg = reg_factory.return_value
        reg.invoke.side_effect = _fake_invoke
        events = list(
            iter_pipeline_events(
                _state(
                    "req_p6_write",
                    "更新活动预算",
                    user_role="admin",
                    user_id="2",
                )
            )
        )

    chart_llm.assert_not_called()
    assert not any(e == "chart" for e, _ in events)
    assert any(e == "write_result" for e, _ in events)
    wr = next(d for e, d in events if e == "write_result")
    assert wr["affected_rows"] == 3
    assert wr.get("sql")
    assert not any(e == "rows" for e, _ in events)
    done = next(d for e, d in events if e == "done")
    assert "repaired" in done
    assert done["repaired"] is False
