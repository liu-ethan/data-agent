from pathlib import Path

from backend.app.resources.domain import (
    ALL_METRICS,
    ALL_TABLES,
    METRICS,
    RELATIONS,
    SLICE_TABLES,
    load_write_ops_raw,
    suggested_questions,
    tenant_id,
)
from backend.app.resources.prompts import render_prompt
from backend.app.resources.sql import SQLITE_DDL_DIR, load_sql


def test_prompt_has_identity_task_constraints_few_shots():
    text = render_prompt("coordinator.intent")
    assert "# 身份" in text
    assert "# 任务" in text
    assert "# 约束" in text
    assert "# Few-shots" in text
    assert "不要调用 interrupt" in text
    assert "本月GMV" in text


def test_all_prompts_render():
    for prompt_id in (
        "coordinator.intent",
        "coordinator.respond",
        "coordinator.title",
        "query.skeleton",
        "query.table_queries",
        "query.schema_gap",
        "coordinator.clarify",
        "write.plan",
    ):
        text = render_prompt(prompt_id)
        assert "# 身份" in text
        assert "# 任务" in text


def test_write_plan_prompt_forbids_interrupt():
    text = render_prompt("write.plan")
    assert "不要调用 interrupt" in text


def test_named_sql_round_trip():
    sql = load_sql("auth.select_user_by_username")
    assert "FROM app_user" in sql
    lock = load_sql("write.lock_target_rows", table="dim_sku")
    assert "`dim_sku`" in lock
    assert SQLITE_DDL_DIR.name == "sqlite"
    assert (SQLITE_DDL_DIR / "users.sql").is_file()


def test_domain_single_source():
    assert tenant_id() == "default"
    assert len(SLICE_TABLES) == 12
    assert len(RELATIONS) == 15
    assert len(METRICS) == 10
    assert ALL_TABLES[0] == "dim_store"
    assert "gmv" in ALL_METRICS
    ops = load_write_ops_raw()
    assert {item["operation_type"] for item in ops} == {
        "update_sku_status",
        "adjust_sku_inventory",
    }
    assert len(suggested_questions()) == 3
    assert Path("seeds/write_ops.yaml").is_file()
    from backend.app.resources.domain import login_meta, think_steps

    login = login_meta()
    assert login["headline"] == "用一句话问经营数字"
    assert len(login["ticker"]) == 10
    assert login["ticker"][0]["label"] == "GMV"
    assert len(login["capabilities"]) == 3
    steps = think_steps()
    assert steps["plan"]["label"] == "理解意图"
    assert steps["q11_execute"]["label"] == "执行查询"
