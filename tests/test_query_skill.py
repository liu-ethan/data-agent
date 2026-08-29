from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.app.catalog.models import (
    CatalogSnapshot,
    MetricSpec,
    SchemaColumn,
    SchemaTable,
    TableRelation,
)
from backend.app.results.store import ResultStore
from backend.app.types import (
    FilterCond,
    PermissionSet,
    QuerySkeleton,
    QueryTask,
    RuntimeContext,
    SchemaBundle,
    SchemaGap,
    SkillErrorCode,
    TimeRange,
)
from scripts.init_sqlite import SQL_DIR, apply_sql

NOW = "2026-08-29T00:00:00+00:00"
TIME_RANGE = TimeRange(
    start="2026-08-01T00:00:00+00:00",
    end="2026-09-01T00:00:00+00:00",
    grain="month",
    label="2026-08",
    source="user",
)
PAID = ["paid", "shipped", "completed"]
ITEM_ORDER = TableRelation(
    left_table="fact_order_item",
    right_table="fact_order",
    left_col="order_id",
    right_col="id",
    cardinality="many_to_one",
    source="fk",
    version=1,
)
ITEM_ORDER_JOIN = {
    "left": "fact_order_item",
    "right": "fact_order",
    "on_left": "order_id",
    "on_right": "id",
    "cardinality": "many_to_one",
}


def _table(name: str) -> SchemaTable:
    return SchemaTable(
        table_name=name,
        business_name=name,
        domain="orders",
        grain_description=name,
    )


def _col(table: str, name: str, data_type: str = "bigint") -> SchemaColumn:
    return SchemaColumn(table_name=table, column_name=name, data_type=data_type)


def _gmv() -> MetricSpec:
    return MetricSpec(
        metric_id="gmv",
        name="GMV",
        version=1,
        grain_table="fact_order_item",
        formula="SUM(oi.price * oi.qty)",
        time_field="fact_order.created_at",
        unit="CNY",
        filters=[FilterCond(field="fact_order.status", op="in", value=list(PAID))],
        deps=[
            "fact_order_item.price",
            "fact_order_item.qty",
            "fact_order.status",
            "fact_order.created_at",
        ],
    )


def _refund() -> MetricSpec:
    return MetricSpec(
        metric_id="refund_rate",
        name="退款率",
        version=1,
        grain_table="fact_refund",
        formula="SUM(r.amount) / NULLIF(SUM(oi.pay_amt), 0)",
        time_field="fact_refund.refunded_at",
        unit="ratio",
        filters=[FilterCond(field="fact_refund.status", op="=", value="success")],
        deps=["fact_refund.amount", "fact_order_item.pay_amt"],
    )


def _catalog(*, sensitive_nick: bool = False) -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version=1,
        tables=[
            _table(n)
            for n in (
                "fact_order",
                "fact_order_item",
                "dim_sku",
                "dim_user",
                "dim_store",
                "fact_refund",
            )
        ],
        columns=[
            _col("fact_order", "id"),
            _col("fact_order", "status", "varchar"),
            _col("fact_order", "created_at", "datetime"),
            _col("fact_order", "store_id"),
            _col("fact_order", "user_id"),
            _col("fact_order_item", "id"),
            _col("fact_order_item", "order_id"),
            _col("fact_order_item", "sku_id"),
            _col("fact_order_item", "price", "decimal"),
            _col("fact_order_item", "qty"),
            _col("fact_order_item", "pay_amt", "decimal"),
            _col("dim_sku", "id"),
            _col("dim_sku", "sku_name", "varchar"),
            _col("dim_store", "id"),
            _col("dim_store", "store_name", "varchar"),
            SchemaColumn(
                table_name="dim_user",
                column_name="nick_name",
                data_type="varchar",
                is_sensitive=sensitive_nick,
            ),
            _col("dim_user", "id"),
            _col("fact_refund", "id"),
            _col("fact_refund", "amount", "decimal"),
            _col("fact_refund", "status", "varchar"),
            _col("fact_refund", "refunded_at", "datetime"),
            _col("fact_refund", "order_item_id"),
        ],
        relations=[
            ITEM_ORDER,
            TableRelation(
                left_table="fact_order",
                right_table="dim_store",
                left_col="store_id",
                right_col="id",
                cardinality="many_to_one",
                source="fk",
                version=1,
            ),
            TableRelation(
                left_table="fact_order",
                right_table="dim_user",
                left_col="user_id",
                right_col="id",
                cardinality="many_to_one",
                source="fk",
                version=1,
            ),
        ],
        metrics=[_gmv(), _refund()],
        write_ops=[],
    )


def _task(**kwargs) -> QueryTask:
    return QueryTask(
        task_id=kwargs.get("task_id", "t1"),
        metric_ids=kwargs.get("metric_ids", ["gmv"]),
        dimensions=kwargs.get("dimensions", []),
        filters=kwargs.get("filters", []),
        time_range=kwargs.get("time_range", TIME_RANGE),
        order_by=kwargs.get("order_by", []),
        limit=kwargs.get("limit"),
        parent_result_id=kwargs.get("parent_result_id"),
        catalog_version=1,
        permission_version=kwargs.get("permission_version", 1),
    )


def _ctx(*, extra_tables: list[str] | None = None, extra_columns: list[str] | None = None) -> RuntimeContext:
    tables = ["fact_order", "fact_order_item", "dim_sku", *(extra_tables or [])]
    columns = [
        "fact_order.*",
        "fact_order_item.*",
        "dim_sku.*",
        *(extra_columns or []),
    ]
    return RuntimeContext(
        tenant_id="default",
        user_id="u1",
        role="analyst",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u1",
            role="analyst",
            allowed_tables=tables,
            allowed_columns=columns,
            allowed_metrics=["gmv", "refund_rate"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th1",
    )


def _bundle(**kwargs) -> SchemaBundle:
    return SchemaBundle(
        tables=kwargs.get("tables", ["fact_order", "fact_order_item"]),
        columns=kwargs.get(
            "columns",
            [
                "fact_order_item.price",
                "fact_order_item.qty",
                "fact_order.status",
                "fact_order.created_at",
            ],
        ),
        joins=kwargs.get("joins", [ITEM_ORDER_JOIN]),
        catalog_version=1,
    )


def _skeleton(**kwargs) -> QuerySkeleton:
    return QuerySkeleton(
        metric_ids=kwargs.get("metric_ids", ["gmv"]),
        select_dims=kwargs.get("select_dims", []),
        from_table=kwargs.get("from_table", "fact_order_item"),
        joins=kwargs.get("joins", [ITEM_ORDER_JOIN]),
        filters=kwargs.get("filters", []),
        time_field=kwargs.get("time_field", "fact_order.created_at"),
        group_by=kwargs.get("group_by", []),
        comparisons=kwargs.get("comparisons", []),
        limit=kwargs.get("limit"),
    )


class FakeQueryLlm:
    def __init__(self, skeletons: QuerySkeleton | list[QuerySkeleton]) -> None:
        self._skeletons = [skeletons] if isinstance(skeletons, QuerySkeleton) else list(skeletons)
        self.skeleton_calls = 0
        self.prompts: list[str] = []
        self.repair_reasons: list[str | None] = []

    def query_skeleton(
        self,
        task: QueryTask,
        bundle: SchemaBundle,
        prompt: str,
        *,
        repair_reason: str | None = None,
    ) -> QuerySkeleton:
        self.skeleton_calls += 1
        self.prompts.append(prompt)
        self.repair_reasons.append(repair_reason)
        index = min(self.skeleton_calls - 1, len(self._skeletons) - 1)
        return self._skeletons[index]

    def table_queries(self, task: QueryTask, prompt: str) -> list[str]:
        return ["GMV 订单行"]

    def schema_gap(
        self,
        *,
        missing_concept: str,
        purpose: str,
        constraints: list[str],
        excluded: list[str],
        prompt: str,
    ) -> SchemaGap:
        return SchemaGap(
            missing_concept=missing_concept,
            purpose=purpose,
            constraints=constraints,
            excluded=excluded,
        )


class ExecuteSpy:
    def __init__(self, inner=None) -> None:
        self.calls: list = []
        self.inner = inner

    def __call__(self, query, ctx, **kwargs):
        self.calls.append(query)
        if self.inner is None:
            raise AssertionError("MySQL execute_read should not be called")
        return self.inner(query, ctx, **kwargs)


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    return ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=1,
        max_rows=100,
        max_bytes=64 * 1024,
    )


def _run(task, ctx, store, llm, catalog=None, **kwargs):
    from backend.app.skills.query.graph import run_query_skill

    return run_query_skill(
        task,
        ctx,
        catalog=catalog or _catalog(),
        store=store,
        llm=llm,
        retrieve_schema_fn=kwargs.get("retrieve_schema_fn", lambda *a, **k: _bundle()),
        execute_read_fn=kwargs.get("execute_read_fn"),
        reload_permissions_fn=kwargs.get(
            "reload_permissions_fn", lambda *a, **k: ctx.permissions
        ),
        engine=kwargs.get("engine"),
        parent_task=kwargs.get("parent_task"),
    )


def _write_parent(store: ResultStore, ctx: RuntimeContext) -> str:
    from backend.app.results.store import ResultWriteMeta

    rid = store.create_writing(
        ResultWriteMeta(
            user_id=ctx.user_id,
            thread_id=ctx.thread_id,
            parent_result_id=None,
            permission_version=ctx.permissions.permission_version,
            catalog_version=1,
            time_range=TIME_RANGE,
            request_time_utc=NOW,
            metric_versions={"gmv": 1},
        )
    )
    store.append_rows(
        rid,
        [
            {"sku_id": "s1", "gmv": 10},
            {"sku_id": "s2", "gmv": 30},
            {"sku_id": "s3", "gmv": 20},
        ],
    )
    store.finalize(rid, data_as_of="2026-08-28T00:00:00+00:00")
    return rid


def test_fake_llm_skeleton_sql_has_audited_formula_and_params(store):
    from backend.app.mysql.execute_read import execute_read
    from tests.test_execute_read import ScriptedEngine

    ctx = _ctx()
    llm = FakeQueryLlm(_skeleton())
    engine = ScriptedEngine(select_rows=[{"gmv": 100}])
    spy = ExecuteSpy(inner=lambda query, ctx, **kw: execute_read(query, ctx, **kw))
    result = _run(_task(), ctx, store, llm, execute_read_fn=spy, engine=engine)

    assert result.ok is True
    assert result.result is not None
    assert spy.calls
    sql = spy.calls[0].sql
    params = spy.calls[0].params
    compact = sql.replace("\n", " ")
    assert "SUM(oi.price * oi.qty)" in compact
    assert ":" in sql
    assert "paid" not in sql
    assert "shipped" not in sql
    assert "completed" not in sql
    assert any(isinstance(v, list) and v == PAID for v in params.values())
    assert TIME_RANGE.start in params.values()
    assert TIME_RANGE.end in params.values()
    assert result.result.preview_rows is None or len(result.result.preview_rows) <= 20


def test_empty_llm_skeleton_still_compiles_task_metrics(store):
    from backend.app.mysql.execute_read import execute_read
    from tests.test_execute_read import ScriptedEngine

    ctx = _ctx()
    llm = FakeQueryLlm(
        _skeleton(
            metric_ids=[],
            select_dims=[],
            from_table="",
            joins=[],
            time_field="",
            group_by=[],
            limit=0,
        )
    )
    engine = ScriptedEngine(select_rows=[{"gmv": 26560}])
    spy = ExecuteSpy(inner=lambda query, ctx, **kw: execute_read(query, ctx, **kw))
    result = _run(_task(), ctx, store, llm, execute_read_fn=spy, engine=engine)

    assert result.ok is True
    assert spy.calls
    sql = spy.calls[0].sql.replace("\n", " ")
    params = spy.calls[0].params
    assert "SUM(oi.price * oi.qty)" in sql
    assert "JOIN fact_order" in sql
    assert "limit" not in params
    assert params.get("limit") != 0


def test_gateway_reject_does_not_execute_mysql(store):
    ctx = _ctx(extra_tables=["dim_user"], extra_columns=["dim_user.nick_name"])
    catalog = _catalog(sensitive_nick=True)
    llm = FakeQueryLlm(
        _skeleton(
            filters=[FilterCond(field="dim_user.nick_name", op="=", value="alice")],
            joins=[
                ITEM_ORDER_JOIN,
                {
                    "left": "fact_order",
                    "right": "dim_user",
                    "on_left": "user_id",
                    "on_right": "id",
                    "cardinality": "many_to_one",
                },
            ],
        )
    )
    bundle = _bundle(
        tables=["fact_order", "fact_order_item", "dim_user"],
        columns=[
            "fact_order_item.price",
            "fact_order_item.qty",
            "fact_order.status",
            "fact_order.created_at",
            "dim_user.nick_name",
        ],
        joins=[
            ITEM_ORDER_JOIN,
            {
                "left": "fact_order",
                "right": "dim_user",
                "on_left": "user_id",
                "on_right": "id",
                "cardinality": "many_to_one",
            },
        ],
    )
    spy = ExecuteSpy()
    result = _run(
        _task(),
        ctx,
        store,
        llm,
        catalog=catalog,
        retrieve_schema_fn=lambda *a, **k: bundle,
        execute_read_fn=spy,
    )

    assert result.ok is False
    assert result.error_code == SkillErrorCode.UNSAFE_SQL
    assert spy.calls == []
    assert llm.skeleton_calls == 1
    assert list(store.results_dir.glob("*.parquet")) == []


def test_too_broad_is_not_repaired(store, monkeypatch):
    from backend.app.gateway.read_policy import GatewayDecision

    ctx = _ctx()
    llm = FakeQueryLlm(_skeleton())
    spy = ExecuteSpy()

    def fake_check(*args, **kwargs):
        return GatewayDecision(ok=False, reason="unconstrained detail scan", kind="too_broad")

    monkeypatch.setattr("backend.app.skills.query.graph.check_read_sql", fake_check)
    result = _run(_task(), ctx, store, llm, execute_read_fn=spy)

    assert result.ok is False
    assert result.error_code == SkillErrorCode.TOO_BROAD
    assert spy.calls == []
    assert llm.skeleton_calls == 1


def test_repairable_unsafe_retries_at_most_twice(store, monkeypatch):
    from backend.app.gateway.read_policy import GatewayDecision

    ctx = _ctx()
    llm = FakeQueryLlm(_skeleton())
    spy = ExecuteSpy()
    checks = {"n": 0}

    def fake_check(*args, **kwargs):
        checks["n"] += 1
        return GatewayDecision(ok=False, reason="column is not in the task allowlist", kind="unsafe")

    monkeypatch.setattr("backend.app.skills.query.graph.check_read_sql", fake_check)
    result = _run(_task(), ctx, store, llm, execute_read_fn=spy)

    assert result.ok is False
    assert result.error_code == SkillErrorCode.UNSAFE_SQL
    assert spy.calls == []
    assert llm.skeleton_calls == 3
    assert llm.repair_reasons[0] is None
    assert llm.repair_reasons[1] is not None
    assert llm.repair_reasons[2] is not None


def test_followup_filter_sets_parent_result_id(store):
    ctx = _ctx()
    parent_id = _write_parent(store, ctx)
    parent_task = _task()
    follow = _task(
        task_id="t2",
        parent_result_id=parent_id,
        filters=[FilterCond(field="gmv", op=">=", value=20)],
        order_by=["gmv DESC"],
        limit=1,
    )
    spy = ExecuteSpy()
    result = _run(
        follow,
        ctx,
        store,
        FakeQueryLlm(_skeleton()),
        execute_read_fn=spy,
        parent_task=parent_task,
    )

    assert result.ok is True
    assert result.result is not None
    assert result.result.parent_result_id == parent_id
    assert result.result.row_count == 1
    assert result.result.preview_rows[0]["sku_id"] == "s2"
    assert spy.calls == []


def test_followup_new_metric_requeries_with_parent_result_id(store):
    from backend.app.skills.query.followup import decide_followup, merge_query_task

    parent = _task()
    follow = _task(
        task_id="t2",
        metric_ids=["gmv", "refund_rate"],
        parent_result_id="parent-1",
    )
    assert decide_followup(follow, parent_task=parent, parent_columns=["sku_id", "gmv"]) == "requery"
    merged = merge_query_task(parent, follow)
    assert merged.parent_result_id == "parent-1"
    assert merged.metric_ids == ["gmv", "refund_rate"]


def test_query_skill_modules_do_not_call_interrupt():
    from backend.app.llm import schemas as llm_schemas
    from backend.app.skills.query import coverage, followup, graph

    for mod in (graph, coverage, followup, llm_schemas):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "interrupt" not in names
        assert "interrupt" not in attrs
        assert "interrupt(" not in src
