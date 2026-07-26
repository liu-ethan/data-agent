import pytest

from app.agent.memory.store import create_session, list_turns, save_turn
from app.agent.memory.turn_display import (
    build_display_payload,
    hydrate_display_from_sql,
)
from app.db.init_db import init_database


@pytest.fixture()
def memory_user_id(tmp_db_path):
    init_database(reset=True)
    return "1"


def test_build_display_payload_caps_rows():
    rows = [{"id": i} for i in range(150)]
    display = build_display_payload(
        columns=["id"],
        rows=rows,
        chart={"type": "bar", "x": "id", "y": "id", "title": "t"},
        repaired=True,
        guardrail_passed=True,
        trace=[{"event": "sql", "summary": "ok"}],
    )
    assert len(display["rows"]) == 100
    assert display["sql_repaired"] is True
    assert display["guardrail_passed"] is True


def test_hydrate_display_from_sql_runs_sandbox(tmp_db_path):
    init_database(reset=True)
    display = hydrate_display_from_sql(
        sql_text=(
            "SELECT order_date, COUNT(DISTINCT id) AS order_count, "
            "SUM(pay_amount) AS gmv FROM orders "
            "WHERE order_date >= date('now', '-30 days') "
            "GROUP BY order_date ORDER BY order_date"
        ),
        question="最近 30 天每天的订单量和 GMV 趋势如何？",
        user_role="analyst",
    )
    assert display is not None
    assert display["guardrail_passed"] is True
    assert "order_count" in display["columns"]
    assert "gmv" in display["columns"]
    assert len(display["rows"]) > 0
    assert display["chart"] is not None
    assert display["chart"]["type"] == "line"
    assert display["chart"].get("series") == ["order_count", "gmv"]


def test_list_turns_hydrates_missing_display(tmp_db_path, memory_user_id):
    sess = create_session(memory_user_id)
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="最近 30 天每天的订单量和 GMV 趋势如何？",
        intent="sales_analysis",
        sql_text=(
            "SELECT order_date, COUNT(DISTINCT id) AS order_count, "
            "SUM(pay_amount) AS gmv FROM orders "
            "WHERE order_date >= date('now', '-30 days') "
            "GROUP BY order_date ORDER BY order_date"
        ),
        slots={
            "metrics": ["order_count", "gmv"],
            "filters": {},
            "group_by": [],
            "time_range": "last_30d",
        },
        result_summary="summary",
        display=None,
    )
    turns = list_turns(sess["id"], memory_user_id, user_role="analyst")
    assert len(turns) == 1
    display = turns[0]["display"]
    assert display is not None
    assert len(display["rows"]) > 0
    assert display["chart"]["type"] == "line"

    # Second load should reuse persisted display (still present).
    turns2 = list_turns(sess["id"], memory_user_id, user_role="analyst")
    assert turns2[0]["display"]["columns"] == display["columns"]
