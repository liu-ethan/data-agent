import pytest
from app.db.init_db import init_database
from app.security.sql_sandbox import SandboxError, sandbox_execute


def test_execute_runs_after_guardrail(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute(
        "SELECT COUNT(*) AS c FROM orders",
        user_role="analyst",
    )
    assert "c" in result.columns
    assert result.rows[0]["c"] > 0


def test_execute_blocked_by_guardrail(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(SandboxError):
        sandbox_execute("SELECT * FROM app_users", user_role="admin")
