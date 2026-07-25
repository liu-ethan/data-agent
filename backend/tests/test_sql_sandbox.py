import pytest
from app.db.init_db import init_database
from app.security.sql_sandbox import SandboxError, sandbox_execute


def test_select_returns_rows(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute(
        "SELECT COUNT(*) AS c FROM orders",
        user_role="analyst",
    )
    assert result.columns == ["c"]
    assert len(result.rows) == 1
    assert result.affected_rows is None
    assert result.is_write is False


def test_blocked_by_guardrail_does_not_execute(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(SandboxError):
        sandbox_execute("DELETE FROM app_users", user_role="admin")


def test_admin_update_returns_affected_rows(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute(
        "UPDATE campaigns SET budget = budget WHERE id IN (SELECT id FROM campaigns LIMIT 1)",
        user_role="admin",
    )
    assert result.is_write is True
    assert result.affected_rows is not None
    assert result.affected_rows >= 0


def test_analyst_write_rejected(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(SandboxError):
        sandbox_execute(
            "UPDATE campaigns SET budget = 1 WHERE id = 1",
            user_role="analyst",
        )


def test_select_without_limit_capped_at_100_rows(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute("SELECT id FROM orders", user_role="analyst")
    assert result.is_write is False
    assert len(result.rows) == 100


def test_admin_write_over_max_rows_rolls_back(tmp_db_path):
    init_database(reset=True)
    before = sandbox_execute(
        "SELECT channel FROM orders WHERE id = 1",
        user_role="admin",
    )
    channel_before = before.rows[0]["channel"]
    with pytest.raises(SandboxError, match=r"100"):
        sandbox_execute(
            "UPDATE orders SET channel = 'sandbox_overflow' WHERE 1 = 1",
            user_role="admin",
        )
    after = sandbox_execute(
        "SELECT channel FROM orders WHERE id = 1",
        user_role="admin",
    )
    assert after.rows[0]["channel"] == channel_before


def test_admin_with_led_insert_uses_write_path(tmp_db_path):
    init_database(reset=True)
    sql = """
    WITH src AS (
        SELECT 88881 AS id, 'with-insert' AS name, 'app' AS channel, 1.0 AS budget
    )
    INSERT INTO campaigns (id, name, channel, budget)
    SELECT id, name, channel, budget FROM src
    """
    result = sandbox_execute(sql, user_role="admin")
    assert result.is_write is True
    assert result.affected_rows == 1
