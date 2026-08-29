from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.app.results.store import ResultStore
from backend.app.types import (
    Intent,
    QuerySkillResult,
    SkillErrorCode,
    WriteSkillResult,
    WriteTask,
)
from scripts.init_sqlite import SQL_DIR, apply_sql
from tests.test_coordinator import (
    FakeCoordinatorLlm,
    FollowupAwareQuerySkill,
    _ctx,
    _graph,
    _interrupt_payload,
    _invoke,
)


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    return ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=24,
        max_rows=100,
        max_bytes=64 * 1024,
    )


def test_query_and_write_skills_do_not_call_interrupt():
    from backend.app.llm import schemas as llm_schemas
    from backend.app.skills.query import coverage, followup
    from backend.app.skills.query import graph as query_graph
    from backend.app.skills.write import graph as write_graph
    from backend.app.skills.write import preview

    for mod in (query_graph, coverage, followup, llm_schemas, write_graph, preview):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "interrupt" not in names
        assert "interrupt" not in attrs
        assert "interrupt(" not in src


def test_hitl_module_body_only_calls_interrupt():
    from backend.app.coordinator import hitl

    src = inspect.getsource(hitl)
    tree = ast.parse(src)
    calls = [
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    assert "interrupt" in calls
    assert "uuid4" not in src
    assert "uuid" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "INSERT" not in src
    assert "operation_id" not in src


def test_write_preview_interrupt_only_in_coordinator(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    executed: list[tuple[str, str]] = []
    prepared: list[WriteTask] = []

    def prepare_write(task: WriteTask, ctx, **kwargs):
        prepared.append(task)
        return WriteSkillResult(
            ok=True,
            status="preview",
            operation_id="op-1",
            preview={
                "operation_id": "op-1",
                "request_hash": "hash-1",
                "rows": [{"id": "1", "status": "on_sale"}],
            },
        )

    def execute_write(operation_id, request_hash, ctx, **kwargs):
        executed.append((operation_id, request_hash))
        return WriteSkillResult(
            ok=True,
            status="committed",
            operation_id=operation_id,
            affected_rows=1,
            audit_id="a1",
        )

    llm = FakeCoordinatorLlm(
        {
            "把SKU 1下架": IntentDraft(
                intent=Intent.WRITE,
                operation_type="update_sku_status",
                object_ids=["1"],
                params={"status": "off_sale"},
            )
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        prepare_write_fn=prepare_write,
        execute_write_fn=execute_write,
    )
    paused = _invoke(graph, "把SKU 1下架", ctx)
    payload = _interrupt_payload(paused)
    assert prepared and not executed
    assert payload["operation_id"] == "op-1"
    assert payload["request_hash"] == "hash-1"

    from backend.app.coordinator import graph as coord_graph
    from backend.app.coordinator import hitl

    assert "interrupt(" in inspect.getsource(hitl)
    assert "interrupt(" in inspect.getsource(coord_graph)

    resumed = _invoke(
        graph,
        "把SKU 1下架",
        ctx,
        resume={"approved": True, "user_id": ctx.user_id},
    )
    assert resumed.get("__interrupt__") in (None, [])
    assert executed == [("op-1", "hash-1")]
    assert resumed.get("operation_id") == "op-1"


def test_write_reject_does_not_execute(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    executed: list = []

    def prepare_write(task, ctx, **kwargs):
        return WriteSkillResult(
            ok=True,
            status="preview",
            operation_id="op-2",
            preview={"operation_id": "op-2", "request_hash": "hash-2"},
        )

    llm = FakeCoordinatorLlm(
        {
            "下架": IntentDraft(
                intent=Intent.WRITE,
                operation_type="update_sku_status",
                object_ids=["1"],
                params={"status": "off_sale"},
            )
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        prepare_write_fn=prepare_write,
        execute_write_fn=lambda *a, **k: executed.append(a) or WriteSkillResult(
            ok=True, status="committed"
        ),
    )
    _invoke(graph, "下架", ctx)
    result = _invoke(graph, "下架", ctx, resume={"approved": False, "user_id": ctx.user_id})
    assert executed == []
    assert result.get("operation_id") in (None, "op-2")
    assert result.get("intent") == Intent.WRITE.value


def test_schema_gap_from_query_skill_interrupts_in_coordinator(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()

    def run_query(task, ctx, **kwargs):
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.SCHEMA_GAP,
            error_message="missing join",
            hitl={"schema_gap": {"missing_concept": "仓储表"}},
        )

    llm = FakeCoordinatorLlm(
        {
            "查库存周转": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                time_text="本月",
            )
        }
    )
    graph = _graph(tmp_path, store, llm, ctx, run_query)
    payload = _interrupt_payload(_invoke(graph, "查库存周转", ctx))
    assert payload.get("error_code") == SkillErrorCode.SCHEMA_GAP.value
    assert "仓储表" in str(payload)
    assert payload.get("message")
    assert "dim_" not in (payload.get("message") or "")
    assert payload.get("candidates")
    assert {item["id"] for item in payload["candidates"]} <= {"gmv", "refund_rate"}


def test_unsafe_sql_interrupts_without_gateway_english(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()

    def run_query(task, ctx, **kwargs):
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.UNSAFE_SQL,
            error_message="column is not in the task allowlist",
        )

    llm = FakeCoordinatorLlm(
        {
            "各品类 GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["dim_category.cat_name"],
            )
        }
    )
    graph = _graph(tmp_path, store, llm, ctx, run_query)
    payload = _interrupt_payload(_invoke(graph, "各品类 GMV", ctx))
    assert payload.get("kind") == "query_error"
    assert payload.get("error_code") == SkillErrorCode.UNSAFE_SQL.value
    assert "allowlist" not in (payload.get("message") or "").lower()
    assert payload.get("candidates")


def test_schema_gap_uses_llm_labels_and_drops_invented_ids(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()

    def run_query(task, ctx, **kwargs):
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.SCHEMA_GAP,
            error_message="dim_category.category_name",
            hitl={"schema_gap": {"missing_concept": "dim_category.category_name"}},
        )

    llm = FakeCoordinatorLlm(
        {
            "各品类销售对比": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["dim_category.category_name"],
            )
        },
        clarify_reply={
            "message": "想看各品类的哪项数据？",
            "candidates": [
                {"id": "gmv", "label": "各品类 GMV"},
                {"id": "invented", "label": "仓储周转"},
            ],
        },
    )
    graph = _graph(tmp_path, store, llm, ctx, run_query)
    payload = _interrupt_payload(_invoke(graph, "各品类销售对比", ctx))
    assert payload["message"] == "想看各品类的哪项数据？"
    assert [item["id"] for item in payload["candidates"]] == ["gmv"]
    assert payload["candidates"][0]["label"] == "各品类 GMV"
    assert llm.clarify_payloads


def test_missing_metric_ids_interrupts_with_metric_buttons(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()

    def run_query(task, ctx, **kwargs):
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.REJECTED,
            error_message="metric_ids required",
        )

    llm = FakeCoordinatorLlm(
        {
            "各品类销售": IntentDraft(
                intent=Intent.QUERY,
                dimensions=["dim_category.cat_name"],
            )
        }
    )
    graph = _graph(tmp_path, store, llm, ctx, run_query)
    payload = _interrupt_payload(_invoke(graph, "各品类销售", ctx))
    assert payload.get("kind") == "clarify"
    assert payload.get("clarify_kind") == "metric"
    assert any(item["id"] == "gmv" for item in payload.get("candidates") or [])
    assert payload.get("message")


def test_hitl_resume_does_not_reresolve_time(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    later = _ctx(request_time_utc="2026-09-01T00:00:00+00:00")
    seen_ranges = []

    def run_query(task, ctx, **kwargs):
        seen_ranges.append(task.time_range)
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.AMBIGUOUS,
            error_message="two paths",
            hitl={"ambiguous": {"reason": "two paths"}},
        )

    llm = FakeCoordinatorLlm(
        {
            "本月GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                time_text="本月",
            )
        }
    )
    graph = _graph(tmp_path, store, llm, ctx, run_query)
    _invoke(graph, "本月GMV", ctx)
    original = seen_ranges[0]
    _invoke(graph, "本月GMV", later, resume={"selected_id": "path-a"})
    assert seen_ranges[-1].start == original.start
    assert seen_ranges[-1].end == original.end
    assert original.label == "2026-08"


def test_permissions_reloaded_on_resume_not_from_checkpoint(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx(permission_version=1)
    versions: list[int] = []
    current = {"v": 1}

    def reload(*a, **k):
        return ctx.permissions.model_copy(update={"permission_version": current["v"]})

    def prepare_write(task, ctx, **kwargs):
        versions.append(ctx.permissions.permission_version)
        return WriteSkillResult(
            ok=True,
            status="preview",
            operation_id="op-p",
            preview={"operation_id": "op-p", "request_hash": "h"},
        )

    def execute_write(operation_id, request_hash, ctx, **kwargs):
        versions.append(ctx.permissions.permission_version)
        return WriteSkillResult(ok=True, status="committed", operation_id=operation_id)

    llm = FakeCoordinatorLlm(
        {
            "下架": IntentDraft(
                intent=Intent.WRITE,
                operation_type="update_sku_status",
                object_ids=["1"],
                params={"status": "off_sale"},
            )
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        prepare_write_fn=prepare_write,
        execute_write_fn=execute_write,
        reload_permissions_fn=reload,
    )
    _invoke(graph, "下架", ctx)
    current["v"] = 2
    _invoke(graph, "下架", ctx, resume={"approved": True, "user_id": ctx.user_id})
    assert versions[0] == 1
    assert versions[-1] == 2


def test_sqlite_saver_persists_query_task(tmp_path, store):
    from langgraph.checkpoint.sqlite import SqliteSaver

    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx(thread_id="th-sqlite")
    ckpt = tmp_path / "checkpoint.sqlite"
    llm = FakeCoordinatorLlm(
        {
            "本月GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["sku_id"],
                time_text="本月",
            ),
            "再加上退款率": IntentDraft(
                intent=Intent.FOLLOWUP,
                metric_ids=["gmv", "refund_rate"],
                dimensions=["sku_id"],
            ),
        }
    )
    skill = FollowupAwareQuerySkill(store)
    with SqliteSaver.from_conn_string(str(ckpt)) as saver:
        graph = _graph(
            tmp_path,
            store,
            llm,
            ctx,
            skill,
            checkpointer=saver,
        )
        _invoke(graph, "本月GMV", ctx)
        snapshot = graph.get_state({"configurable": {"thread_id": ctx.thread_id}})
        values = snapshot.values
        assert values.get("result_id")
        assert values.get("query_task") is not None

    with SqliteSaver.from_conn_string(str(ckpt)) as saver:
        graph = _graph(
            tmp_path,
            store,
            llm,
            ctx,
            skill,
            checkpointer=saver,
        )
        result = _invoke(graph, "再加上退款率", ctx)
        assert result.get("intent") == Intent.FOLLOWUP.value
        assert skill.mysql == 2
