from unittest.mock import patch

from app.agent.nodes.sql_repairer import sql_repairer


def test_repair_sets_flag_and_sql():
    state = {
        "question": "各渠道 GMV",
        "generated_sql": "SELECT pay_ammount FROM orders",
        "error": "no such column: pay_ammount",
        "relevant_tables": ["orders"],
        "relevant_columns": {"orders": ["id", "pay_amount", "channel"]},
        "metric_specs": [],
        "repaired": False,
    }
    with patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        return_value="SELECT channel, SUM(pay_amount) AS gmv FROM orders GROUP BY channel",
    ):
        out = sql_repairer(state)
    assert out["repaired"] is True
    assert out["error"] is None
    assert "pay_amount" in out["generated_sql"]
    assert "pay_ammount" not in out["generated_sql"]


def test_repair_failure_keeps_error():
    state = {
        "question": "x",
        "generated_sql": "SELECT 1",
        "error": "boom",
        "relevant_tables": [],
        "relevant_columns": {},
        "repaired": False,
    }
    with patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        side_effect=ValueError("no key"),
    ):
        out = sql_repairer(state)
    assert out["repaired"] is True
    assert out.get("error")
