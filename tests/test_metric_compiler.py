from __future__ import annotations

from sqlglot import exp

from backend.app.catalog.models import MetricSpec
from backend.app.compiler.metric_compiler import compile
from backend.app.gateway.ast import parse_mysql, scope_physical_tables
from backend.app.types import FilterCond, QuerySkeleton, TimeRange

PAID = ["paid", "shipped", "completed"]
TIME = TimeRange(
    start="2026-08-01T00:00:00+08:00",
    end="2026-09-01T00:00:00+08:00",
    grain="month",
    label="2026-08",
    source="user",
)
ITEM_ORDER_JOIN = {
    "left": "fact_order_item",
    "right": "fact_order",
    "on_left": "order_id",
    "on_right": "id",
    "cardinality": "many_to_one",
}


def _metric(
    metric_id: str,
    *,
    name: str,
    grain_table: str,
    formula: str,
    time_field: str,
    unit: str,
    deps: list[str],
    filters: list[FilterCond] | None = None,
    version: int = 1,
    needs_tables: list[str] | None = None,
) -> MetricSpec:
    return MetricSpec(
        metric_id=metric_id,
        name=name,
        version=version,
        grain_table=grain_table,
        formula=formula,
        time_field=time_field,
        unit=unit,
        filters=filters or [FilterCond(field="fact_order.status", op="in", value=list(PAID))],
        deps=deps,
        needs_tables=needs_tables or [],
    )


def _gmv(*, version: int = 1) -> MetricSpec:
    return _metric(
        "gmv",
        name="GMV",
        grain_table="fact_order_item",
        formula="SUM(oi.price * oi.qty)",
        time_field="fact_order.created_at",
        unit="CNY",
        version=version,
        deps=[
            "fact_order_item.price",
            "fact_order_item.qty",
            "fact_order.status",
            "fact_order.created_at",
        ],
    )


def _order_count(*, version: int = 1) -> MetricSpec:
    return _metric(
        "order_count",
        name="订单量",
        grain_table="fact_order",
        formula="COUNT(DISTINCT o.id)",
        time_field="fact_order.created_at",
        unit="count",
        version=version,
        deps=["fact_order.id", "fact_order.status", "fact_order.created_at"],
    )


def _aov() -> MetricSpec:
    return _metric(
        "aov",
        name="客单价",
        grain_table="fact_order",
        formula="SUM(oi.pay_amt) / NULLIF(COUNT(DISTINCT o.id), 0)",
        time_field="fact_order.paid_at",
        unit="CNY",
        deps=["fact_order_item.pay_amt", "fact_order.id"],
    )


def _skeleton(metric_ids: list[str], **kwargs) -> QuerySkeleton:
    return QuerySkeleton(
        metric_ids=metric_ids,
        select_dims=kwargs.get("select_dims", []),
        from_table=kwargs.get("from_table", "fact_order_item"),
        joins=kwargs.get("joins", [ITEM_ORDER_JOIN]),
        filters=kwargs.get("filters", []),
        time_field=kwargs.get("time_field", "fact_order.created_at"),
        group_by=kwargs.get("group_by", []),
        comparisons=kwargs.get("comparisons", []),
        limit=kwargs.get("limit"),
    )


def _cte_selects(sql: str) -> dict[str, exp.Select]:
    tree = parse_mysql(sql)
    out: dict[str, exp.Select] = {}
    for cte in tree.find_all(exp.CTE):
        query = cte.this
        if isinstance(query, exp.Subquery):
            query = query.this
        if isinstance(query, exp.Select):
            out[cte.alias_or_name] = query
    return out


def _cte_tables(select: exp.Select) -> set[str]:
    return scope_physical_tables(select, set())


def test_same_input_compiles_to_byte_identical_sql_and_params():
    skeleton = _skeleton(["gmv", "order_count"])
    metrics = [_gmv(), _order_count()]
    first = compile(skeleton, metrics, TIME)
    second = compile(skeleton, metrics, TIME)
    assert first.sql.encode("utf-8") == second.sql.encode("utf-8")
    assert list(first.params.items()) == list(second.params.items())


def test_metric_version_change_changes_sql():
    skeleton = _skeleton(["gmv", "order_count"])
    v1 = compile(skeleton, [_gmv(version=1), _order_count()], TIME)
    v2 = compile(skeleton, [_gmv(version=2), _order_count()], TIME)
    assert v1.sql != v2.sql


def test_gmv_and_order_count_use_two_grain_ctes_without_item_in_order_from():
    compiled = compile(_skeleton(["gmv", "order_count"]), [_gmv(), _order_count()], TIME)
    ctes = _cte_selects(compiled.sql)
    assert len(ctes) >= 2

    item_cte = None
    order_cte = None
    for name, select in ctes.items():
        tables = _cte_tables(select)
        if "fact_order_item" in tables:
            item_cte = name
        elif "fact_order" in tables:
            order_cte = name
            assert "fact_order_item" not in tables

    assert item_cte is not None
    assert order_cte is not None
    assert "oi.price" in compiled.sql
    assert "oi.qty" in compiled.sql
    assert "COUNT(DISTINCT o.id)" in compiled.sql.replace("\n", " ")


def test_filter_values_go_into_params_not_sql_literals():
    skeleton = _skeleton(
        ["gmv"],
        filters=[FilterCond(field="fact_order.store_id", op="=", value=7)],
        joins=[
            ITEM_ORDER_JOIN,
            {
                "left": "fact_order",
                "right": "dim_store",
                "on_left": "store_id",
                "on_right": "id",
                "cardinality": "many_to_one",
            },
        ],
    )
    compiled = compile(skeleton, [_gmv()], TIME)
    assert "paid" not in compiled.sql
    assert "shipped" not in compiled.sql
    assert "completed" not in compiled.sql
    assert "'7'" not in compiled.sql
    assert " 7" not in compiled.sql.replace(":store_id", "")
    paid_in_params = any(
        isinstance(value, list) and value == PAID for value in compiled.params.values()
    )
    assert paid_in_params
    assert 7 in compiled.params.values()
    assert TIME.start in compiled.params.values()
    assert TIME.end in compiled.params.values()
    assert ":" in compiled.sql


def test_ignores_formula_like_strings_from_skeleton():
    skeleton = _skeleton(
        ["gmv"],
        select_dims=["SUM(oi.price * 999)"],
        group_by=["SUM(oi.price * 999)"],
    )
    compiled = compile(skeleton, [_gmv()], TIME)
    assert "999" not in compiled.sql
    assert "SUM(oi.price * oi.qty)" in compiled.sql.replace("\n", " ")


def test_aov_aggregates_each_grain_before_dividing():
    compiled = compile(_skeleton(["aov"]), [_aov()], TIME)
    ctes = _cte_selects(compiled.sql)
    order_select = None
    item_select = None
    for select in ctes.values():
        tables = _cte_tables(select)
        if "fact_order_item" in tables:
            item_select = select
        elif "fact_order" in tables:
            order_select = select
            assert "fact_order_item" not in tables
    assert item_select is not None
    assert order_select is not None
    assert "NULLIF" in compiled.sql.upper()
    order_sql = order_select.sql(dialect="mysql")
    assert "COUNT(DISTINCT" in order_sql.upper().replace(" ", "") or "COUNT(DISTINCT" in order_sql


def test_yoy_does_not_divide_by_zero_when_prior_is_zero():
    compiled = compile(_skeleton(["gmv"], comparisons=["yoy"]), [_gmv()], TIME)
    assert "NULLIF" in compiled.sql.upper()
    assert any(key.startswith("yoy_") for key in compiled.params)
    assert TIME.start in compiled.params.values()


def test_yoy_status_filter_applies_to_both_periods():
    compiled = compile(_skeleton(["gmv"], comparisons=["yoy"]), [_gmv()], TIME)
    select = next(iter(_cte_selects(compiled.sql).values()))
    where = select.args["where"].this
    assert not isinstance(where, exp.Or)
