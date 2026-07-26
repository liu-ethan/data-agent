from unittest.mock import patch

from app.agent.chart_planner import plan_chart


def test_empty_rows_returns_none():
    assert plan_chart("q", ["a", "b"], []) is None


def test_empty_columns_returns_none():
    assert plan_chart("q", [], [{"a": 1}]) is None


def test_llm_valid_bar_accepted():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}, {"channel": "web", "gmv": 5}]
    with patch(
        "app.agent.chart_planner.chat_completion",
        return_value='{"type":"bar","x":"channel","y":"gmv","title":"渠道 GMV"}',
    ) as m:
        out = plan_chart("各渠道 GMV", cols, rows)
    assert out == {
        "type": "bar",
        "x": "channel",
        "y": "gmv",
        "title": "渠道 GMV",
    }
    m.assert_called_once()


def test_llm_invalid_falls_back_to_heuristic_bar():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}, {"channel": "web", "gmv": 5}]
    with patch(
        "app.agent.chart_planner.chat_completion",
        return_value='{"type":"zzz","x":"nope","y":"gmv","title":"x"}',
    ):
        out = plan_chart("各渠道 GMV Top5", cols, rows)
    assert out is not None
    assert out["type"] == "bar"
    assert out["x"] == "channel"
    assert out["y"] == "gmv"


def test_llm_exception_falls_back():
    cols = ["order_date", "gmv"]
    rows = [
        {"order_date": "2024-01-01", "gmv": 1},
        {"order_date": "2024-01-02", "gmv": 2},
    ]
    with patch(
        "app.agent.chart_planner.chat_completion",
        side_effect=RuntimeError("boom"),
    ):
        out = plan_chart("最近趋势", cols, rows)
    assert out is not None
    assert out["type"] == "line"
    assert out["x"] == "order_date"
    assert out["y"] == "gmv"


def test_empty_question_skips_llm_uses_heuristic():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}]
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = plan_chart("", cols, rows)
    m.assert_not_called()
    assert out is not None
    assert out["type"] == "bar"


def test_heuristic_pie_for_rate_column():
    cols = ["pay_method", "success_rate"]
    rows = [
        {"pay_method": "alipay", "success_rate": 0.9},
        {"pay_method": "wechat", "success_rate": 0.8},
    ]
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = plan_chart("", cols, rows)
    m.assert_not_called()
    assert out["type"] == "pie"
    assert out["x"] == "pay_method"
    assert out["y"] == "success_rate"


from app.agent.nodes.chart_planner import chart_planner_node


def test_node_skips_on_write():
    with patch("app.agent.nodes.chart_planner.plan_chart") as m:
        out = chart_planner_node(
            {
                "question": "update",
                "is_write": True,
                "columns": [],
                "rows": [],
            }
        )
    m.assert_not_called()
    assert out == {"chart": None}


def test_node_skips_on_empty_rows():
    with patch("app.agent.nodes.chart_planner.plan_chart") as m:
        out = chart_planner_node(
            {
                "question": "q",
                "is_write": False,
                "columns": ["a"],
                "rows": [],
            }
        )
    m.assert_not_called()
    assert out == {"chart": None}


def test_node_calls_plan_chart():
    chart = {"type": "bar", "x": "a", "y": "b", "title": "t"}
    with patch(
        "app.agent.nodes.chart_planner.plan_chart",
        return_value=chart,
    ) as m:
        out = chart_planner_node(
            {
                "question": "q",
                "is_write": False,
                "columns": ["a", "b"],
                "rows": [{"a": "x", "b": 1}],
                "slots": {"metrics": ["gmv"]},
            }
        )
    m.assert_called_once()
    assert out == {"chart": chart}


def test_heuristic_multi_metric_trend_sets_series():
    cols = ["order_date", "order_count", "gmv"]
    rows = [
        {"order_date": "2026-06-26", "order_count": 3, "gmv": 100},
        {"order_date": "2026-06-27", "order_count": 2, "gmv": 200},
    ]
    with patch(
        "app.agent.chart_planner.chat_completion",
        side_effect=RuntimeError("skip llm"),
    ):
        out = plan_chart("最近 30 天每天的订单量和 GMV 趋势如何？", cols, rows)
    assert out is not None
    assert out["type"] == "line"
    assert out["x"] == "order_date"
    assert out["y"] == "order_count"
    assert out["series"] == ["order_count", "gmv"]
