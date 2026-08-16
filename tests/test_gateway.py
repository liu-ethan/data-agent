from datetime import datetime, timedelta, timezone

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.gateways import ReadGateway
from backend.app.models import PermissionContext, QueryPlan, QuerySpec
from backend.app.testing import build_test_gateway
from sqlglot import exp, parse_one


def plan(sql: str, *, objects=None, time_field="orders.paid_at"):
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    spec = QuerySpec(query_id="q", metric_refs=["gmv"], time_field=time_field,
        time_range={"start": start, "end": start + timedelta(days=1), "timezone": "Asia/Shanghai"},
        allowed_object_ids=objects or ["obj_orders", "obj_order_items"], expected_columns=["gmv"])
    return QueryPlan(query_plan_id="p", query_spec=spec, candidate_sql=sql,
        parameters={"status": "PAID", "start": "2026-08-15 00:00:00", "end": "2026-08-16 00:00:00", "max_rows": 1000},
        catalog_version="catalog_v1", permission_policy_version="policy_v1")


def permission():
    return PermissionContext(user_id="u", roles=["USER"], scope_mode="ALLOWLIST",
        allowed_shop_ids=["shop_001"], policy_version="policy_v1")


def test_gateway_executes_and_injects_scope():
    gateway = build_test_gateway()
    result = gateway.execute(plan("SELECT ROUND(SUM(oi.item_paid_amount), 2) AS gmv FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end"), permission())
    assert result.status.value == "SUCCESS"
    assert result.summary and result.summary.columns == ["gmv"]
    assert result.trace.rls_injected is True


def test_gateway_rejects_dangerous_sql_and_empty_scope():
    gateway = build_test_gateway()
    blocked = gateway.execute(plan("DROP TABLE orders"), permission())
    assert blocked.error_code == "SQL_FORBIDDEN_OPERATION"
    no_scope = PermissionContext(user_id="u", scope_mode="NONE", policy_version="policy_v1")
    blocked = gateway.execute(plan("SELECT 1 AS gmv FROM orders WHERE paid_at>=:start AND paid_at<:end"), no_scope)
    assert blocked.error_code == "PERMISSION_DENIED"


def test_rls_is_injected_into_each_nested_fact_scope():
    gateway = build_test_gateway()
    sql = """WITH paid AS (
        SELECT o.order_id, o.shop_id, o.paid_at FROM orders o
        WHERE o.paid_at >= :start AND o.paid_at < :end
    )
    SELECT SUM(oi.item_paid_amount) AS gmv
    FROM paid p JOIN order_items oi ON oi.order_id = p.order_id"""
    nested_plan = plan(sql, objects=["obj_orders", "obj_order_items"])
    tables, _ = gateway._validate_sql(sql, nested_plan)
    rewritten, params = gateway._inject_rls(sql, nested_plan.parameters, permission(), tables)
    tree = parse_one(rewritten, read="mysql")
    scoped_aliases = {column.table for column in tree.find_all(exp.Column)
                      if column.name == "shop_id" and column.parent and isinstance(column.parent, exp.In)}
    assert scoped_aliases == {"o", "oi"}
    assert params["rls_shop_0"] == "shop_001"


def test_result_aliases_use_ast_and_gmv_filter_is_mandatory():
    gateway = build_test_gateway()
    sql = "SELECT SUM(oi.item_paid_amount) AS GMV FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.paid_at>=:start AND o.paid_at<:end"
    unsafe = plan(sql)
    tables, columns = gateway._validate_sql(sql, unsafe)
    with pytest.raises(RuntimeAgentError, match="paid-order status"):
        gateway._validate_query_spec(sql, tables, columns, unsafe)

    localized = plan(sql.replace("AS GMV", "AS `成交额`").replace("WHERE", "WHERE o.status=:status AND"))
    localized.query_spec.expected_columns = ["成交额"]
    tables, columns = gateway._validate_sql(localized.candidate_sql, localized)
    gateway._validate_query_spec(localized.candidate_sql, tables, columns, localized)


class _DataFailure:
    def __init__(self, error_code):
        self.error_code = error_code

    def explain(self, sql, parameters):
        return 1.0, 1

    def fetch(self, sql, parameters):
        raise RuntimeAgentError(self.error_code, "driver detail", retryable=True)


class _Rows:
    def explain(self, sql, parameters):
        return 1.0, 1

    def fetch(self, sql, parameters):
        return [{"gmv": 1}]


class _Results:
    def save(self, rows, *, owner_user_id=None):
        return "result_test"


class _BrokenResults:
    def save(self, rows, *, owner_user_id=None):
        raise OSError("storage unavailable")


def test_gateway_preserves_timeout_and_execution_error_semantics():
    sql = "SELECT SUM(oi.item_paid_amount) AS gmv FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end"
    timeout = ReadGateway(data=_DataFailure("QUERY_TIMEOUT"), results=_Results()).execute(
        plan(sql), permission())
    assert timeout.status.value == "TIMEOUT"
    assert timeout.error_code == "QUERY_TIMEOUT"

    failed = ReadGateway(
        data=_DataFailure("QUERY_EXECUTION_FAILED"), results=_Results()).execute(
            plan(sql), permission())
    assert failed.status.value == "FAILED"
    assert failed.error_code == "QUERY_EXECUTION_FAILED"


def test_gateway_distinguishes_result_persistence_failure():
    sql = "SELECT SUM(oi.item_paid_amount) AS gmv FROM orders o JOIN order_items oi ON oi.order_id=o.order_id WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end"
    failed = ReadGateway(data=_Rows(), results=_BrokenResults()).execute(
        plan(sql), permission())
    assert failed.status.value == "FAILED"
    assert failed.error_code == "RESULT_PERSIST_FAILED"
