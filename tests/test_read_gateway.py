from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.catalog.models import (
    CatalogSnapshot,
    MetricSpec,
    SchemaColumn,
    SchemaTable,
    TableRelation,
)
from backend.app.gateway.read_policy import check_read_sql
from backend.app.types import CompiledQuery, FilterCond, PermissionSet, QueryTask, TimeRange


def _table(name: str) -> SchemaTable:
    return SchemaTable(
        table_name=name,
        business_name=name,
        domain="orders",
        grain_description=name,
    )


def _col(table: str, name: str, data_type: str = "bigint", *, sensitive: bool = False) -> SchemaColumn:
    return SchemaColumn(
        table_name=table,
        column_name=name,
        data_type=data_type,
        is_sensitive=sensitive,
    )


ITEM_ORDER = TableRelation(
    left_table="fact_order_item",
    right_table="fact_order",
    left_col="order_id",
    right_col="id",
    cardinality="many_to_one",
    source="fk",
    version=1,
)
ORDER_USER = TableRelation(
    left_table="fact_order",
    right_table="dim_user",
    left_col="user_id",
    right_col="id",
    cardinality="many_to_one",
    source="fk",
    version=1,
)


def _catalog(*, sensitive_nick: bool = False) -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version=1,
        tables=[_table(n) for n in ("fact_order", "fact_order_item", "dim_sku", "dim_user", "fact_ad_spend")],
        columns=[
            _col("fact_order", "id"),
            _col("fact_order", "order_no", "varchar"),
            _col("fact_order", "user_id"),
            _col("fact_order", "status", "varchar"),
            _col("fact_order", "created_at", "datetime"),
            _col("fact_order_item", "id"),
            _col("fact_order_item", "order_id"),
            _col("fact_order_item", "sku_id"),
            _col("fact_order_item", "price", "decimal"),
            _col("fact_order_item", "qty"),
            _col("dim_sku", "id"),
            _col("dim_sku", "sku_name", "varchar"),
            _col("dim_user", "id"),
            _col("dim_user", "nick_name", "varchar", sensitive=sensitive_nick),
            _col("dim_user", "first_order_at", "datetime"),
            _col("fact_ad_spend", "id"),
            _col("fact_ad_spend", "amount", "decimal"),
        ],
        relations=[ITEM_ORDER, ORDER_USER],
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
                deps=[
                    "fact_order_item.price",
                    "fact_order_item.qty",
                    "fact_order.status",
                    "fact_order.created_at",
                ],
                needs_tables=[],
            ),
            MetricSpec(
                metric_id="order_count",
                name="订单量",
                version=1,
                grain_table="fact_order",
                formula="COUNT(DISTINCT o.id)",
                time_field="fact_order.created_at",
                unit="count",
                filters=[],
                deps=["fact_order.id", "fact_order.created_at"],
                needs_tables=[],
            ),
            MetricSpec(
                metric_id="new_customers",
                name="新客数",
                version=1,
                grain_table="fact_order",
                formula="COUNT(DISTINCT o.user_id)",
                time_field="dim_user.first_order_at",
                unit="count",
                filters=[],
                deps=["dim_user.first_order_at", "fact_order.user_id"],
                needs_tables=[],
            ),
        ],
        write_ops=[],
    )


TIME = TimeRange(
    start="2026-08-01T00:00:00+08:00",
    end="2026-09-01T00:00:00+08:00",
    label="2026-08",
)


def _task(*metric_ids: str, filters: list[FilterCond] | None = None) -> QueryTask:
    return QueryTask(
        task_id="t1",
        metric_ids=list(metric_ids) or ["gmv"],
        dimensions=[],
        filters=filters or [],
        time_range=TIME,
        catalog_version=1,
        permission_version=1,
    )


@pytest.fixture
def catalog() -> CatalogSnapshot:
    return _catalog()


@pytest.fixture
def catalog_with_sensitive_nick() -> CatalogSnapshot:
    return _catalog(sensitive_nick=True)


@pytest.fixture
def task() -> QueryTask:
    return _task("gmv")


GMV_SQL = (
    "SELECT oi.price, SUM(oi.price * oi.qty) AS gmv "
    "FROM fact_order_item oi "
    "JOIN fact_order o ON oi.order_id = o.id "
    "WHERE o.created_at >= :start AND o.created_at < :end "
    "GROUP BY oi.price"
)
GMV_PARAMS = {"start": "2026-08-01", "end": "2026-09-01"}


def test_rejects_select_star(task, catalog):
    q = CompiledQuery(sql="SELECT * FROM fact_order", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_qualified_star(task, catalog):
    q = CompiledQuery(sql="SELECT t.* FROM fact_order t LIMIT 10", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_sensitive_column(task, catalog_with_sensitive_nick):
    q = CompiledQuery(sql="SELECT nick_name FROM dim_user WHERE id = :id", params={"id": 1})
    d = check_read_sql(q, _task("new_customers"), catalog_with_sensitive_nick, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_inlined_filter_value(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM dim_sku WHERE sku_name = 'x' OR 1=1", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False


def test_rejects_inlined_task_filter_literal(catalog):
    task = _task("gmv", filters=[FilterCond(field="dim_sku.sku_name", op="=", value="widget")])
    q = CompiledQuery(sql="SELECT id FROM dim_sku WHERE sku_name = 'widget' LIMIT 10", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_multiple_statements(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM fact_order; SELECT id FROM dim_sku", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_non_select(task, catalog):
    q = CompiledQuery(sql="UPDATE fact_order SET status = :s WHERE id = :id", params={"s": "x", "id": 1})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_parse_failure(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM fact_order INTO OUTFILE '/tmp/x'", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM fact_order FOR UPDATE",
        "SELECT id FROM fact_order LOCK IN SHARE MODE",
        "SELECT SLEEP(1)",
        "SELECT BENCHMARK(1, 1)",
        "SELECT UPDATEXML(1, 'x', 'y')",
        "SELECT LOAD_FILE('/etc/passwd')",
    ],
)
def test_rejects_locking_and_dangerous_functions(task, catalog, sql):
    q = CompiledQuery(sql=sql, params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_table_outside_task_allowlist(task, catalog):
    q = CompiledQuery(
        sql="SELECT amount FROM fact_ad_spend WHERE id = :id LIMIT 10",
        params={"id": 1},
    )
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_column_outside_permissions(task, catalog):
    perms = PermissionSet(
        tenant_id="default",
        user_id="u1",
        role="analyst",
        allowed_tables=["fact_order"],
        allowed_columns=["data-agent-ecommerce.fact_order.id"],
        allowed_metrics=["gmv"],
        allowed_write_ops=[],
        catalog_version=1,
        permission_version=1,
    )
    q = CompiledQuery(
        sql=(
            "SELECT order_no FROM fact_order "
            "WHERE created_at >= :start AND created_at < :end LIMIT 10"
        ),
        params=GMV_PARAMS,
    )
    d = check_read_sql(q, task, catalog, allowed_joins=[], permissions=perms)
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_join_not_in_allowed_joins(task, catalog):
    q = CompiledQuery(sql=GMV_SQL, params=GMV_PARAMS)
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"


def test_rejects_fanout_aggregation_without_grain_distinct(catalog):
    sql = (
        "SELECT COUNT(*) AS cnt "
        "FROM fact_order o "
        "JOIN fact_order_item oi ON oi.order_id = o.id "
        "WHERE o.created_at >= :start AND o.created_at < :end"
    )
    q = CompiledQuery(sql=sql, params=GMV_PARAMS)
    d = check_read_sql(q, _task("order_count"), catalog, allowed_joins=[ITEM_ORDER])
    assert d.ok is False and d.kind == "unsafe"


def test_allows_fanout_when_aggregated_with_grain_distinct(catalog):
    sql = (
        "SELECT COUNT(DISTINCT o.id) AS cnt "
        "FROM fact_order o "
        "JOIN fact_order_item oi ON oi.order_id = o.id "
        "WHERE o.created_at >= :start AND o.created_at < :end"
    )
    q = CompiledQuery(sql=sql, params=GMV_PARAMS)
    d = check_read_sql(q, _task("order_count"), catalog, allowed_joins=[ITEM_ORDER])
    assert d.ok is True and d.kind == "ok"


def test_unconstrained_detail_is_too_broad(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM fact_order", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "too_broad"


def test_detail_with_huge_limit_is_too_broad(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM fact_order LIMIT 5000001", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "too_broad"


def test_explain_rows_over_limit_is_too_broad(task, catalog):
    q = CompiledQuery(
        sql=(
            "SELECT o.id FROM fact_order o "
            "WHERE o.created_at >= :start AND o.created_at < :end LIMIT 10"
        ),
        params=GMV_PARAMS,
    )
    d = check_read_sql(
        q, task, catalog, allowed_joins=[], explain_rows=5_000_001, max_explain_rows=5_000_000
    )
    assert d.ok is False and d.kind == "too_broad"


def test_parameterized_aggregate_join_is_ok(task, catalog):
    q = CompiledQuery(sql=GMV_SQL, params=GMV_PARAMS)
    d = check_read_sql(q, task, catalog, allowed_joins=[ITEM_ORDER], explain_rows=100)
    assert d.ok is True and d.kind == "ok"
    assert d.reason is None


def test_does_not_rewrite_sql(task, catalog):
    sql = GMV_SQL
    q = CompiledQuery(sql=sql, params=GMV_PARAMS)
    check_read_sql(q, task, catalog, allowed_joins=[ITEM_ORDER], explain_rows=100)
    assert q.sql == sql


def _connect_or_skip():
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    from backend.app.mysql.pool import get_engine

    try:
        engine = get_engine("reader")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"MySQL reader unreachable: {exc}")


@pytest.mark.integration
def test_estimate_explain_rows_uses_mysql_not_sqlite():
    from backend.app.gateway.explain import estimate_explain_rows

    engine = _connect_or_skip()
    rows = estimate_explain_rows(
        "SELECT id FROM dim_sku WHERE id = :id",
        {"id": 1},
        engine=engine,
    )
    assert isinstance(rows, int)
    assert rows >= 0
