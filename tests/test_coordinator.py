from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.app.catalog.models import CatalogSnapshot, MetricSpec, SchemaColumn, SchemaTable
from backend.app.results.store import ResultStore, ResultWriteMeta
from backend.app.skills.query.followup import decide_followup, local_filter_spec
from backend.app.types import (
    FilterCond,
    Intent,
    PermissionSet,
    QuerySkillResult,
    QueryTask,
    ResultSummary,
    RuntimeContext,
    TimeRange,
    WriteSkillResult,
    WriteTask,
)
from scripts.init_sqlite import SQL_DIR, apply_sql

NOW = "2026-08-29T00:00:00+00:00"
LATER = "2026-08-29T18:00:00+00:00"
TIME_RANGE = TimeRange(
    start="2026-08-01T00:00:00+00:00",
    end="2026-09-01T00:00:00+00:00",
    grain="month",
    label="2026-08",
    source="user",
)


def _catalog() -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version=1,
        tables=[
            SchemaTable(table_name=n, business_name=n, domain="d", grain_description=n)
            for n in ("dim_sku", "dim_store", "fact_order_item", "fact_refund")
        ],
        columns=[
            SchemaColumn(table_name="dim_sku", column_name="id", data_type="bigint"),
            SchemaColumn(table_name="dim_sku", column_name="sku_name", data_type="varchar"),
            SchemaColumn(table_name="dim_store", column_name="id", data_type="bigint"),
            SchemaColumn(table_name="fact_order_item", column_name="sku_id", data_type="bigint"),
        ],
        relations=[],
        metrics=[
            MetricSpec(
                metric_id="gmv",
                name="GMV",
                version=1,
                grain_table="fact_order_item",
                formula="SUM(oi.price * oi.qty)",
                time_field="fact_order.created_at",
                unit="CNY",
                filters=[],
                deps=["fact_order_item.price"],
            ),
            MetricSpec(
                metric_id="refund_rate",
                name="退款率",
                version=1,
                grain_table="fact_refund",
                formula="SUM(r.amount) / NULLIF(SUM(oi.pay_amt), 0)",
                time_field="fact_refund.refunded_at",
                unit="ratio",
                filters=[],
                deps=["fact_refund.amount"],
            ),
        ],
        write_ops=[],
    )


def _ctx(
    *,
    user_id: str = "u1",
    role: str = "operator",
    request_time_utc: str = NOW,
    permission_version: int = 1,
    thread_id: str = "th-coord",
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id="default",
        user_id=user_id,
        role=role,  # type: ignore[arg-type]
        request_time_utc=request_time_utc,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id=user_id,
            role=role,  # type: ignore[arg-type]
            allowed_tables=["dim_sku", "dim_store", "fact_order_item", "fact_refund"],
            allowed_columns=["dim_sku.*", "dim_store.*", "fact_order_item.*"],
            allowed_metrics=["gmv", "refund_rate"],
            allowed_write_ops=["update_sku_status", "adjust_sku_inventory"],
            catalog_version=1,
            permission_version=permission_version,
        ),
        thread_id=thread_id,
    )


def _task(**kwargs) -> QueryTask:
    return QueryTask(
        task_id=kwargs.get("task_id", "qt-1"),
        metric_ids=kwargs.get("metric_ids", ["gmv"]),
        dimensions=kwargs.get("dimensions", ["store_id"]),
        filters=kwargs.get("filters", []),
        time_range=kwargs.get("time_range", TIME_RANGE),
        order_by=kwargs.get("order_by", []),
        limit=kwargs.get("limit"),
        parent_result_id=kwargs.get("parent_result_id"),
        catalog_version=1,
        permission_version=kwargs.get("permission_version", 1),
    )


def _write_rows(store: ResultStore, ctx: RuntimeContext, rows: list[dict], *, parent=None) -> ResultSummary:
    rid = store.create_writing(
        ResultWriteMeta(
            user_id=ctx.user_id,
            thread_id=ctx.thread_id,
            parent_result_id=parent,
            permission_version=ctx.permissions.permission_version,
            catalog_version=1,
            time_range=TIME_RANGE,
            request_time_utc=ctx.request_time_utc,
            metric_versions={"gmv": 1},
        )
    )
    store.append_rows(rid, rows)
    return store.finalize(rid, data_as_of="2026-08-28T00:00:00+00:00")


class FollowupAwareQuerySkill:
    """Delegates filter vs requery to the query Skill helper. Coordinator must not decide."""

    def __init__(self, store: ResultStore) -> None:
        self.store = store
        self.mysql = 0
        self.duckdb = 0
        self.tasks: list[QueryTask] = []
        self.parents: list[QueryTask | None] = []

    def __call__(self, task: QueryTask, ctx: RuntimeContext, *, parent_task=None, **kwargs):
        self.tasks.append(task)
        self.parents.append(parent_task)
        if task.parent_result_id and parent_task is not None:
            parent = self.store.read_page(task.parent_result_id, ctx)
            kind = decide_followup(
                task, parent_task=parent_task, parent_columns=parent.columns
            )
            if kind == "filter":
                self.duckdb += 1
                child = self.store.filter_local(
                    task.parent_result_id,
                    local_filter_spec(task, parent.columns),
                    ctx,
                )
                return QuerySkillResult(ok=True, result=self.store.read_page(child, ctx))
            self.mysql += 1
            rows = [{"sku_id": "s1", "gmv": 100, "refund_rate": 0.1}]
            return QuerySkillResult(
                ok=True,
                result=_write_rows(self.store, ctx, rows, parent=task.parent_result_id),
            )
        self.mysql += 1
        rows = (
            [{"store_id": "st1", "gmv": 100}, {"store_id": "st2", "gmv": 50}]
            if "store_id" in task.dimensions
            else [{"sku_id": "s1", "gmv": 10}, {"sku_id": "s2", "gmv": 30}]
        )
        return QuerySkillResult(ok=True, result=_write_rows(self.store, ctx, rows))


class FakeCoordinatorLlm:
    def __init__(self, drafts: dict[str, Any], *, answer: str = "GMV 为 100") -> None:
        self.drafts = drafts
        self.answer = answer
        self.response_prompts: list[str] = []
        self.response_facts: list[dict[str, Any]] = []
        self.classify_prompts: list[str] = []

    def classify_intent(self, message: str, prompt: str, *, has_parent_query: bool):
        from backend.app.coordinator.intent import IntentDraft

        self.classify_prompts.append(prompt)
        draft = self.drafts[message]
        if isinstance(draft, IntentDraft):
            return draft
        return draft

    def compose_answer(self, prompt: str, facts: dict[str, Any]) -> str:
        self.response_prompts.append(prompt)
        self.response_facts.append(facts)
        return self.answer


def _interrupt_payload(result: dict) -> dict:
    items = result.get("__interrupt__") or []
    assert items, f"expected interrupt, got {result!r}"
    value = items[0].value
    assert isinstance(value, dict)
    return value


def _runtime_db(tmp_path: Path) -> Path:
    db = tmp_path / "runtime.sqlite"
    apply_sql(db, SQL_DIR / "runtime.sql")
    return db


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


def _graph(tmp_path, store, llm, ctx, query_skill, **kwargs):
    from backend.app.coordinator.graph import build_coordinator_graph

    return build_coordinator_graph(
        llm=llm,
        catalog=kwargs.get("catalog", _catalog()),
        store=store,
        run_query_fn=query_skill,
        prepare_write_fn=kwargs.get("prepare_write_fn"),
        execute_write_fn=kwargs.get("execute_write_fn"),
        lookup_products_fn=kwargs.get("lookup_products_fn"),
        lookup_metrics_fn=kwargs.get("lookup_metrics_fn"),
        lookup_time_fn=kwargs.get("lookup_time_fn"),
        reload_permissions_fn=kwargs.get(
            "reload_permissions_fn", lambda *a, **k: ctx.permissions
        ),
        runtime_db=kwargs.get("runtime_db", _runtime_db(tmp_path)),
        checkpointer=kwargs.get("checkpointer", MemorySaver()),
    )


def _invoke(graph, message: str, ctx: RuntimeContext, *, resume=None):
    from backend.app.coordinator.graph import invoke_coordinator

    return invoke_coordinator(graph, message, ctx, resume=resume)


def test_ambiguous_product_interrupt_has_stable_ids(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    llm = FakeCoordinatorLlm(
        {
            "苹果卖得怎么样": IntentDraft(
                intent=Intent.CLARIFY,
                clarify_kind="product",
                clarify_query="苹果",
            )
        }
    )
    hits = [{"id": "sku-101", "label": "红富士苹果"}]

    def lookup_products(query, permissions, **kwargs):
        assert query == "苹果"
        assert "dim_sku" in permissions.allowed_tables
        return list(hits)

    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        lookup_products_fn=lookup_products,
    )
    result = _invoke(graph, "苹果卖得怎么样", ctx)
    payload = _interrupt_payload(result)
    candidates = payload.get("candidates") or []
    assert candidates
    assert all(item.get("id") for item in candidates)
    assert {item["id"] for item in candidates} == {"sku-101"}


def test_empty_product_lookup_does_not_fabricate_candidates(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    llm = FakeCoordinatorLlm(
        {
            "查一下不存在的商品": IntentDraft(
                intent=Intent.CLARIFY,
                clarify_kind="product",
                clarify_query="不存在的商品",
            )
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        lookup_products_fn=lambda *a, **k: [],
    )
    result = _invoke(graph, "查一下不存在的商品", ctx)
    payload = _interrupt_payload(result)
    assert payload.get("candidates") == []
    text = str(payload)
    assert "未查到" in text or "not_found" in payload.get("status", "")
    assert "示例" not in text
    assert "fake" not in text.lower()


def test_candidates_module_has_no_fabricate_path():
    from backend.app.coordinator import candidates

    src = inspect.getsource(candidates)
    tree = ast.parse(src)
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not any("fabricat" in n.lower() or n.lower().startswith("fake") for n in names)
    assert "示例商品" not in src
    assert "dummy_sku" not in src.lower()


def test_followup_filter_uses_duckdb_not_mysql(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    skill = FollowupAwareQuerySkill(store)
    llm = FakeCoordinatorLlm(
        {
            "本月各门店GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["store_id"],
                time_text="本月",
            ),
            "按门店筛一下": IntentDraft(
                intent=Intent.FOLLOWUP,
                metric_ids=["gmv"],
                dimensions=["store_id"],
                filters=[FilterCond(field="store_id", op="=", value="st1")],
            ),
        },
        answer="GMV 为 100",
    )
    graph = _graph(tmp_path, store, llm, ctx, skill)
    first = _invoke(graph, "本月各门店GMV", ctx)
    assert first.get("__interrupt__") in (None, [])
    assert skill.mysql == 1
    assert skill.duckdb == 0
    assert first.get("intent") == Intent.QUERY.value

    second = _invoke(graph, "按门店筛一下", ctx)
    assert second.get("intent") == Intent.FOLLOWUP.value
    assert skill.duckdb == 1
    assert skill.mysql == 1
    assert skill.parents[-1] is not None
    assert skill.tasks[-1].parent_result_id
    assert second.get("result_id")


def test_followup_new_metric_requeries_mysql(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    skill = FollowupAwareQuerySkill(store)
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
    graph = _graph(tmp_path, store, llm, ctx, skill)
    _invoke(graph, "本月GMV", ctx)
    assert skill.mysql == 1
    second = _invoke(graph, "再加上退款率", ctx)
    assert second.get("intent") == Intent.FOLLOWUP.value
    assert skill.mysql == 2
    assert skill.duckdb == 0
    assert "refund_rate" in skill.tasks[-1].metric_ids


def test_query_and_write_in_one_sentence_is_unsupported(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    skill = FollowupAwareQuerySkill(store)
    llm = FakeCoordinatorLlm(
        {
            "查GMV并把SKU下架": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                operation_type="update_sku_status",
                object_ids=["1"],
                params={"status": "off_sale"},
            )
        }
    )
    graph = _graph(tmp_path, store, llm, ctx, skill)
    result = _invoke(graph, "查GMV并把SKU下架", ctx)
    assert result.get("intent") == Intent.UNSUPPORTED.value
    assert skill.mysql == 0
    assert result.get("__interrupt__") in (None, [])


def test_respond_prompt_excludes_preview_rows_and_grounds_numbers(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    skill = FollowupAwareQuerySkill(store)
    llm = FakeCoordinatorLlm(
        {
            "本月GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["sku_id"],
                time_text="本月",
            )
        },
        answer="GMV 为 9999999",
    )
    graph = _graph(tmp_path, store, llm, ctx, skill)
    result = _invoke(graph, "本月GMV", ctx)
    assert llm.response_prompts
    prompt = llm.response_prompts[0]
    assert "preview_rows" not in prompt
    assert ".parquet" not in prompt
    facts = llm.response_facts[0]
    assert "preview_rows" not in facts
    assert facts.get("result_id")
    assert facts.get("metric_versions")
    assert result.get("answer")
    assert "9999999" not in result["answer"]


def test_followup_without_time_keeps_parent_range(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    later = _ctx(request_time_utc=LATER)
    skill = FollowupAwareQuerySkill(store)
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
    graph = _graph(tmp_path, store, llm, ctx, skill)
    _invoke(graph, "本月GMV", ctx)
    parent_range = skill.tasks[0].time_range
    _invoke(graph, "再加上退款率", later)
    assert skill.tasks[-1].time_range.start == parent_range.start
    assert skill.tasks[-1].time_range.end == parent_range.end


def test_runtime_sqlite_only_upserts_thread(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    runtime_db = _runtime_db(tmp_path)
    llm = FakeCoordinatorLlm(
        {
            "本月GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["sku_id"],
                time_text="本月",
            )
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        FollowupAwareQuerySkill(store),
        runtime_db=runtime_db,
    )
    _invoke(graph, "本月GMV", ctx)
    with sqlite3.connect(runtime_db) as conn:
        threads = conn.execute("SELECT thread_id, user_id FROM thread").fetchall()
        tasks = conn.execute("SELECT COUNT(*) FROM task").fetchone()[0]
        hitl = conn.execute("SELECT COUNT(*) FROM hitl_interrupt").fetchone()[0]
    assert threads == [(ctx.thread_id, ctx.user_id)]
    assert tasks == 0
    assert hitl == 0


def test_previous_skus_fill_write_object_ids(tmp_path, store):
    from backend.app.coordinator.intent import IntentDraft

    ctx = _ctx()
    skill = FollowupAwareQuerySkill(store)
    prepared: list[WriteTask] = []

    def prepare_write(task: WriteTask, ctx, **kwargs):
        prepared.append(task)
        return WriteSkillResult(
            ok=True,
            status="preview",
            operation_id="op-sku",
            preview={
                "operation_id": "op-sku",
                "request_hash": "hash-sku",
                "object_ids": list(task.object_ids),
            },
        )

    llm = FakeCoordinatorLlm(
        {
            "本月GMV": IntentDraft(
                intent=Intent.QUERY,
                metric_ids=["gmv"],
                dimensions=["sku_id"],
                time_text="本月",
            ),
            "把这些SKU下架": IntentDraft(
                intent=Intent.WRITE,
                operation_type="update_sku_status",
                refer_previous_skus=True,
                params={"status": "off_sale"},
            ),
        }
    )
    graph = _graph(
        tmp_path,
        store,
        llm,
        ctx,
        skill,
        prepare_write_fn=prepare_write,
        execute_write_fn=lambda *a, **k: WriteSkillResult(ok=True, status="committed"),
    )
    _invoke(graph, "本月GMV", ctx)
    result = _invoke(graph, "把这些SKU下架", ctx)
    payload = _interrupt_payload(result)
    assert prepared
    assert set(prepared[-1].object_ids) == {"s1", "s2"}
    assert payload.get("operation_id") == "op-sku"


def test_lookup_metrics_filters_by_permission():
    from backend.app.coordinator.candidates import lookup_metrics

    ctx = _ctx()
    denied = ctx.permissions.model_copy(update={"allowed_metrics": ["gmv"]})
    hits = lookup_metrics("退款", _catalog(), denied)
    assert hits == []
    allowed = lookup_metrics("退款", _catalog(), ctx.permissions)
    assert allowed
    assert {h["id"] for h in allowed} == {"refund_rate"}
