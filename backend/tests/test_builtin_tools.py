from app.db.init_db import init_database
from app.tools.builtins import ensure_builtins_registered
from app.tools.schemas import ToolContext


def _ctx(role="analyst", node="Test"):
    return ToolContext(
        request_id="r",
        trace_id="t",
        session_id="s",
        user_id="1",
        user_role=role,
        node=node,
    )


def test_five_tools_registered():
    reg = ensure_builtins_registered()
    names = {t.name for t in reg.list_tools()}
    assert names >= {
        "query_schema",
        "query_knowledge",
        "retrieve_metric_definition",
        "validate_sql",
        "execute_sql",
        "render_chart",
    }


def test_query_schema_hides_sensitive_for_analyst(tmp_db_path):
    init_database(reset=True)
    reg = ensure_builtins_registered()
    out = reg.invoke("query_schema", {}, context=_ctx("analyst"))
    assert out.ok
    users = next(t for t in out.data["tables"] if t["name"] == "users")
    col_names = {c["name"] for c in users["columns"]}
    assert "phone" not in col_names


def test_retrieve_metric_definition(tmp_db_path):
    reg = ensure_builtins_registered()
    ok = reg.invoke(
        "retrieve_metric_definition",
        {"metric": "gmv"},
        context=_ctx(),
    )
    assert ok.ok
    assert "expression" in ok.data
    bad = reg.invoke(
        "retrieve_metric_definition",
        {"metric": "not_a_metric"},
        context=_ctx(),
    )
    assert bad.ok is False


def test_query_knowledge_metric_by_alias(tmp_db_path):
    reg = ensure_builtins_registered()
    out = reg.invoke(
        "query_knowledge",
        {"query": "销售额"},
        context=_ctx(),
    )
    assert out.ok
    assert out.data["kind"] == "metric"
    assert out.data["metric"]["key"] == "gmv"


def test_validate_and_execute_sql_read(tmp_db_path, monkeypatch, tmp_path):
    init_database(reset=True)
    monkeypatch.setattr(
        "app.tools.audit.audit_log_path", lambda: tmp_path / "audit.jsonl"
    )
    reg = ensure_builtins_registered()
    sql = "SELECT COUNT(*) AS c FROM orders"
    v = reg.invoke("validate_sql", {"sql": sql}, context=_ctx(node="SQLGuardrail"))
    assert v.ok and v.data["ok"] is True
    ex = reg.invoke("execute_sql", {"sql": sql}, context=_ctx(node="SQLExecutor"))
    assert ex.ok
    assert ex.data["columns"] == ["c"]
    assert (tmp_path / "audit.jsonl").exists()


def test_execute_sql_admin_write_audited(tmp_db_path, monkeypatch, tmp_path):
    init_database(reset=True)
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.tools.audit.audit_log_path", lambda: log)
    reg = ensure_builtins_registered()
    sql = "UPDATE campaigns SET budget = budget WHERE id IN (SELECT id FROM campaigns LIMIT 1)"
    ex = reg.invoke("execute_sql", {"sql": sql}, context=_ctx("admin", "SQLExecutor"))
    assert ex.ok
    assert ex.data.get("affected_rows") is not None
    text = log.read_text(encoding="utf-8")
    assert "execute_sql" in text
    assert "high" in text or "affected_rows" in text


def test_validate_sql_failure_returns_structured_error():
    reg = ensure_builtins_registered()
    sql = "UPDATE orders SET status = 'x' WHERE id = 1"
    out = reg.invoke(
        "validate_sql",
        {"sql": sql},
        context=_ctx("analyst", "SQLGuardrail"),
    )
    assert out.ok is False
    assert out.data["ok"] is False
    assert out.error
    assert out.data.get("reason") or out.error


def test_ensure_builtins_registered_idempotent():
    expected = {
        "query_schema",
        "query_knowledge",
        "retrieve_metric_definition",
        "validate_sql",
        "execute_sql",
        "render_chart",
    }
    reg1 = ensure_builtins_registered()
    names1 = {t.name for t in reg1.list_tools()}
    len1 = len(reg1.list_tools())
    reg2 = ensure_builtins_registered()
    names2 = {t.name for t in reg2.list_tools()}
    len2 = len(reg2.list_tools())
    assert reg1 is reg2
    assert names1 == expected
    assert names2 == expected
    assert len1 == 6
    assert len2 == 6


def test_render_chart_returns_config():
    reg = ensure_builtins_registered()
    out = reg.invoke(
        "render_chart",
        {
            "columns": ["channel", "gmv"],
            "rows": [{"channel": "app", "gmv": 1}],
            "title": "渠道 GMV",
        },
        context=_ctx(),
    )
    assert out.ok
    assert out.data["type"] in {"bar", "line", "pie", "table"}
    assert "x" in out.data and "y" in out.data


def test_render_chart_uses_plan_chart_heuristic():
    from unittest.mock import patch

    reg = ensure_builtins_registered()
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = reg.invoke(
            "render_chart",
            {
                "columns": ["channel", "gmv"],
                "rows": [{"channel": "app", "gmv": 1}],
                "title": "渠道 GMV",
            },
            context=_ctx(),
        )
    m.assert_not_called()
    assert out.ok
    assert out.data["type"] == "bar"
