import pytest
from app.db.init_db import init_database
from app.agent.sql_executor import execute_sql, GuardrailError


def test_execute_runs_after_guardrail(tmp_db_path):
    init_database(reset=True)
    cols, rows = execute_sql(
        "SELECT COUNT(*) AS c FROM orders",
        user_role="analyst",
    )
    assert "c" in cols
    assert rows[0]["c"] > 0


def test_execute_blocked_by_guardrail(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(GuardrailError):
        execute_sql("SELECT * FROM app_users", user_role="admin")
