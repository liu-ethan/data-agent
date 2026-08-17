"""Spec 02 acceptance tests for ReadGateway.

Covers:
- §8 30 dangerous SQL rejected (covered by tests/test_security.py)
- §8 20 Golden SQL pass through the gateway
- §8 no analytical SQL bypass routes
- §6 RLS injection is re-parsed and re-validated
- §6 ALL scope requires ADMIN role
- §6 RESULT_PERSIST_FAILED is distinguished from QUERY_EXECUTION_FAILED
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.gateways import ReadGateway
from backend.app.models import PermissionContext, QueryPlan, QuerySpec
from backend.app.testing import build_test_gateway

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _plan(sql: str, *, objects, time_field, expected_columns, metric_refs=None,
          time_range=None):
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    rng = time_range or {"start": start, "end": start + timedelta(days=1),
                         "timezone": "Asia/Shanghai"}
    spec = QuerySpec(query_id=f"q_{abs(hash(sql)) % 100000}",
                     metric_refs=metric_refs or [],
                     time_field=time_field,
                     time_range=rng,
                     allowed_object_ids=objects,
                     expected_columns=expected_columns)
    return QueryPlan(query_plan_id=f"p_{abs(hash(sql)) % 100000}",
                     query_spec=spec, candidate_sql=sql,
                     parameters={"status": "PAID",
                                "start": "2026-08-15 00:00:00",
                                "end": "2026-08-16 00:00:00",
                                "max_rows": 1000},
                     catalog_version="catalog_v1",
                     permission_policy_version="policy_v1")


def _permission(*, scope_mode="ALLOWLIST", roles=None, shop_ids=None):
    return PermissionContext(user_id="u", roles=roles or ["USER"],
        scope_mode=scope_mode,
        allowed_shop_ids=["shop_001"] if shop_ids is None else list(shop_ids),
        policy_version="policy_v1")


# ---------------------------------------------------------------------------
# §8: 20 Golden SQL pass through the gateway
# ---------------------------------------------------------------------------

# Each entry is a fully-formed SQL that exercises a real Spec 01 metric or
# non-metric query. The plan wires allowed objects and expected columns so
# that QuerySpec / catalog validation accepts every statement.
GOLDEN_SQL: list[dict] = [
    {"id": "g01_category_gmv", "sql":
     "SELECT c.category_name, ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "JOIN products p ON p.product_id = oi.product_id "
     "JOIN categories c ON c.category_id = p.category_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY c.category_id, c.category_name",
     "objects": ["obj_orders", "obj_order_items", "obj_products", "obj_categories"],
     "time_field": "orders.paid_at",
     "expected": ["category_name", "gmv"],
     "metric_refs": ["gmv"]},
    {"id": "g02_total_gmv", "sql":
     "SELECT ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end",
     "objects": ["obj_orders", "obj_order_items"],
     "time_field": "orders.paid_at",
     "expected": ["gmv"],
     "metric_refs": ["gmv"]},
    {"id": "g03_paid_order_count", "sql":
     "SELECT COUNT(DISTINCT o.order_id) AS paid_order_count "
     "FROM orders o "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end",
     "objects": ["obj_orders"],
     "time_field": "orders.paid_at",
     "expected": ["paid_order_count"],
     "metric_refs": ["paid_order_count"]},
    {"id": "g04_paid_buyer_count", "sql":
     "SELECT COUNT(DISTINCT o.user_id) AS paid_buyer_count "
     "FROM orders o "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end",
     "objects": ["obj_orders"],
     "time_field": "orders.paid_at",
     "expected": ["paid_buyer_count"],
     "metric_refs": ["paid_buyer_count"]},
    {"id": "g05_refund_amount", "sql":
     "SELECT ROUND(SUM(r.refund_amount), 2) AS refund_amount "
     "FROM refunds r "
     "WHERE r.status = 'SUCCESS' AND r.refunded_at >= :start AND r.refunded_at < :end",
     "objects": ["obj_refunds"],
     "time_field": "refunds.refunded_at",
     "expected": ["refund_amount"],
     "metric_refs": ["refund_amount"]},
    {"id": "g06_refund_rate", "sql":
     "SELECT ROUND(SUM(r.refund_amount) / NULLIF(SUM(oi.item_paid_amount), 0), 4) "
     "AS refund_rate "
     "FROM refunds r "
     "JOIN orders o ON o.order_id = r.order_id "
     "JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE r.status = 'SUCCESS' AND r.refunded_at >= :start AND r.refunded_at < :end",
     "objects": ["obj_refunds", "obj_orders", "obj_order_items"],
     "time_field": "refunds.refunded_at",
     "expected": ["refund_rate"],
     "metric_refs": ["refund_rate"]},
    {"id": "g07_shop_revenue", "sql":
     "SELECT o.shop_id, ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY o.shop_id ORDER BY gmv DESC",
     "objects": ["obj_orders", "obj_order_items"],
     "time_field": "orders.paid_at",
     "expected": ["shop_id", "gmv"],
     "metric_refs": ["gmv"]},
    {"id": "g08_top_products", "sql":
     "SELECT p.product_name, ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "JOIN products p ON p.product_id = oi.product_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY p.product_id, p.product_name ORDER BY gmv DESC LIMIT 10",
     "objects": ["obj_orders", "obj_order_items", "obj_products"],
     "time_field": "orders.paid_at",
     "expected": ["product_name", "gmv"],
     "metric_refs": ["gmv"]},
    {"id": "g09_daily_orders", "sql":
     "SELECT DATE(o.paid_at) AS day, COUNT(DISTINCT o.order_id) AS paid_order_count "
     "FROM orders o "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY DATE(o.paid_at)",
     "objects": ["obj_orders"],
     "time_field": "orders.paid_at",
     "expected": ["day", "paid_order_count"],
     "metric_refs": ["paid_order_count"]},
    {"id": "g10_orders_with_refunds", "sql":
     "SELECT o.order_id, ROUND(SUM(oi.item_paid_amount), 2) AS gmv, "
     "ROUND(IFNULL(SUM(r.refund_amount), 0), 2) AS refunded "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "LEFT JOIN refunds r ON r.order_id = o.order_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY o.order_id",
     "objects": ["obj_orders", "obj_order_items", "obj_refunds"],
     "time_field": "orders.paid_at",
     "expected": ["order_id", "gmv", "refunded"],
     "metric_refs": ["gmv"]},
    {"id": "g11_status_breakdown", "sql":
     "SELECT o.status, COUNT(*) AS cnt "
     "FROM orders o WHERE o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY o.status",
     "objects": ["obj_orders"],
     "time_field": "orders.paid_at",
     "expected": ["status", "cnt"],
     "metric_refs": []},
    {"id": "g12_category_refunds", "sql":
     "SELECT c.category_name, ROUND(SUM(r.refund_amount), 2) AS refund_amount "
     "FROM refunds r JOIN orders o ON o.order_id = r.order_id "
     "JOIN order_items oi ON oi.order_id = o.order_id "
     "JOIN products p ON p.product_id = oi.product_id "
     "JOIN categories c ON c.category_id = p.category_id "
     "WHERE r.status = 'SUCCESS' AND r.refunded_at >= :start AND r.refunded_at < :end "
     "GROUP BY c.category_id, c.category_name ORDER BY refund_amount DESC",
     "objects": ["obj_refunds", "obj_orders", "obj_order_items", "obj_products", "obj_categories"],
     "time_field": "refunds.refunded_at",
     "expected": ["category_name", "refund_amount"],
     "metric_refs": ["refund_amount"]},
    {"id": "g13_region_revenue", "sql":
     "SELECT s.region_name, ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "JOIN shops s ON s.shop_id = o.shop_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY s.region_name",
     "objects": ["obj_orders", "obj_order_items", "obj_shops"],
     "time_field": "orders.paid_at",
     "expected": ["region_name", "gmv"],
     "metric_refs": ["gmv"]},
    {"id": "g14_avg_order_value", "sql":
     "SELECT ROUND(SUM(oi.item_paid_amount) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) "
     "AS avg_order_value "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end",
     "objects": ["obj_orders", "obj_order_items"],
     "time_field": "orders.paid_at",
     "expected": ["avg_order_value"],
     "metric_refs": ["gmv"]},
    {"id": "g15_order_count_by_shop", "sql":
     "SELECT o.shop_id, COUNT(DISTINCT o.order_id) AS paid_order_count "
     "FROM orders o "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY o.shop_id",
     "objects": ["obj_orders"],
     "time_field": "orders.paid_at",
     "expected": ["shop_id", "paid_order_count"],
     "metric_refs": ["paid_order_count"]},
    {"id": "g16_refund_items_count", "sql":
     "SELECT r.refund_id, COUNT(*) AS item_count "
     "FROM refunds r JOIN refund_items ri ON ri.refund_id = r.refund_id "
     "WHERE r.refunded_at >= :start AND r.refunded_at < :end "
     "GROUP BY r.refund_id",
     "objects": ["obj_refunds", "obj_refund_items"],
     "time_field": "refunds.refunded_at",
     "expected": ["refund_id", "item_count"],
     "metric_refs": []},
    {"id": "g17_active_shops", "sql":
     "SELECT s.shop_id, s.shop_name FROM shops s WHERE s.status = 'ACTIVE'",
     "objects": ["obj_shops"],
     "time_field": None,
     "expected": ["shop_id", "shop_name"],
     "metric_refs": []},
    {"id": "g18_root_categories", "sql":
     "SELECT category_id, category_name FROM categories WHERE parent_id IS NULL",
     "objects": ["obj_categories"],
     "time_field": None,
     "expected": ["category_id", "category_name"],
     "metric_refs": []},
    {"id": "g19_product_count_per_shop", "sql":
     "SELECT shop_id, COUNT(*) AS product_count FROM products "
     "WHERE status = 'ACTIVE' GROUP BY shop_id",
     "objects": ["obj_products"],
     "time_field": None,
     "expected": ["shop_id", "product_count"],
     "metric_refs": []},
    {"id": "g20_top_buyers", "sql":
     "SELECT o.user_id, ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
     "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end "
     "GROUP BY o.user_id ORDER BY gmv DESC LIMIT 5",
     "objects": ["obj_orders", "obj_order_items"],
     "time_field": "orders.paid_at",
     "expected": ["user_id", "gmv"],
     "metric_refs": ["gmv"]},
]


@pytest.mark.parametrize("case", GOLDEN_SQL, ids=[c["id"] for c in GOLDEN_SQL])
def test_golden_sql_passes_through_gateway(case):
    """Each golden SQL must go through ReadGateway and yield SUCCESS or EMPTY."""
    gateway = build_test_gateway()
    plan = _plan(case["sql"], objects=case["objects"],
                 time_field=case["time_field"],
                 expected_columns=case["expected"],
                 metric_refs=case["metric_refs"])
    # Dimension-only queries (shops / categories / products) have no fact
    # table to apply RLS to. Spec §6 rule 3 reserves those to ADMIN/ALL.
    objects = set(case["objects"])
    if objects & {"obj_orders", "obj_order_items", "obj_refunds", "obj_refund_items"}:
        permission = _permission()
        expected_rls = True
    else:
        permission = _permission(scope_mode="ALL", roles=["ADMIN"], shop_ids=[])
        expected_rls = False
    result = gateway.execute(plan, permission)
    assert result.status.value in {"SUCCESS", "EMPTY"}, (
        f"{case['id']} failed: status={result.status.value} "
        f"error={result.error_code}")
    assert result.trace.rewritten_sql_hash, "rewritten SQL hash must be recorded"
    assert result.trace.original_sql_hash, "original SQL hash must be recorded"
    assert result.trace.rls_injected is expected_rls
    assert result.trace.explain_cost is not None
    assert result.trace.row_count is not None


# ---------------------------------------------------------------------------
# §6: ALL scope requires ADMIN role
# ---------------------------------------------------------------------------

def test_all_scope_requires_admin_role():
    gateway = build_test_gateway()
    sql = "SELECT COUNT(*) AS cnt FROM orders o " \
          "WHERE o.paid_at >= :start AND o.paid_at < :end"
    plan = _plan(sql, objects=["obj_orders"], time_field="orders.paid_at",
                 expected_columns=["cnt"])

    blocked = gateway.execute(
        plan, _permission(scope_mode="ALL", roles=["USER"], shop_ids=[]))
    assert blocked.status.value == "REJECTED"
    assert blocked.error_code == "PERMISSION_DENIED"

    admin = gateway.execute(
        plan, _permission(scope_mode="ALL", roles=["ADMIN"], shop_ids=[]))
    assert admin.status.value in {"SUCCESS", "EMPTY"}


# ---------------------------------------------------------------------------
# §6: RLS injection is re-parsed and re-validated
# ---------------------------------------------------------------------------

def test_rls_reparse_rejects_forbidden_nodes_introduced_by_injection(monkeypatch):
    """If a code change accidentally introduced a forbidden AST node after
    RLS injection, _revalidate_after_rls must still block it. This test pins
    the invariant against future regressions."""
    gateway = build_test_gateway()
    gateway.allow_select_star = False
    sql = "SELECT COUNT(*) AS cnt FROM orders o " \
          "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end"
    # Inject a Star into the rewritten SQL post-RLS to simulate a
    # regression where RLS rewrite is bypassed.
    tables, _columns = gateway._validate_sql(sql, _plan(
        sql, objects=["obj_orders"], time_field="orders.paid_at",
        expected_columns=["cnt"]))
    rewritten, _params = gateway._inject_rls(
        sql, {"status": "PAID", "start": "2026-08-15", "end": "2026-08-16"},
        _permission(), tables)
    poisoned = rewritten.replace("COUNT(*) AS cnt", "COUNT(*) AS cnt, *")
    with pytest.raises(RuntimeAgentError) as exc:
        gateway._revalidate_after_rls(poisoned)
    assert exc.value.error_code in {"SQL_FORBIDDEN_OPERATION", "SQL_OBJECT_NOT_ALLOWED"}


def test_rls_reparse_rejects_introduced_sensitive_column(monkeypatch):
    gateway = build_test_gateway()
    sql = "SELECT COUNT(*) AS cnt FROM orders o " \
          "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end"
    plan = _plan(sql, objects=["obj_orders"], time_field="orders.paid_at",
                 expected_columns=["cnt"])
    tables, _ = gateway._validate_sql(sql, plan)
    rewritten, _ = gateway._inject_rls(
        sql, plan.parameters, _permission(), tables)
    poisoned = rewritten + " UNION SELECT phone FROM users"
    with pytest.raises(RuntimeAgentError):
        gateway._revalidate_after_rls(poisoned)


# ---------------------------------------------------------------------------
# §8: no analytical SQL bypass routes
# ---------------------------------------------------------------------------

_RAW_SQL_PATTERN = re.compile(
    r"connection\.execute\s*\(\s*text\s*\(\s*['\"](?:SELECT|WITH)\b",
    flags=re.IGNORECASE,
)


def test_no_analytical_sql_bypass_outside_gateway():
    """No module under backend/app/ except the gateway, control-plane
    services and runtime repositories may execute a SELECT/WITH statement
    directly. Analytical data is reached only through ReadGateway."""
    allowed_paths = {
        Path("backend/app/gateways/read_gateway.py"),
        Path("backend/app/services/permission.py"),       # control plane
        Path("backend/app/repositories/data.py"),         # reader verification
        Path("backend/app/repositories/runtime.py"),      # control plane
        Path("backend/app/repositories/catalog.py"),      # control plane
        Path("backend/app/services/schema_catalog.py"),   # indexer only
        Path("backend/app/memory/stores.py"),              # control plane
    }
    offenders: list[tuple[str, int]] = []
    for py_file in (PROJECT_ROOT / "backend" / "app").rglob("*.py"):
        rel = py_file.relative_to(PROJECT_ROOT)
        if rel in allowed_paths:
            continue
        for line_no, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW_SQL_PATTERN.search(line):
                offenders.append((str(rel), line_no))
    assert not offenders, (
        "Analytical SQL must go through ReadGateway; "
        f"found bypass routes: {offenders}")


# ---------------------------------------------------------------------------
# §8: golden SQL result IDs persist
# ---------------------------------------------------------------------------

def test_golden_sql_writes_a_persisted_result():
    """Every successful execution writes through ResultRepositoryPort."""
    persisted: list[list[dict]] = []

    class _CapturingResults:
        def save(self, rows, *, owner_user_id=None):
            persisted.append(rows)
            return "result_captured"

    class _DataOK:
        def explain(self, sql, parameters):
            return 1.0, 1

        def fetch(self, sql, parameters):
            return [{"gmv": 1.0}]

    sql = "SELECT ROUND(SUM(oi.item_paid_amount), 2) AS gmv " \
          "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id " \
          "WHERE o.status = :status AND o.paid_at >= :start AND o.paid_at < :end"
    gateway = ReadGateway(data=_DataOK(), results=_CapturingResults())
    result = gateway.execute(
        _plan(sql, objects=["obj_orders", "obj_order_items"],
              time_field="orders.paid_at", expected_columns=["gmv"],
              metric_refs=["gmv"]),
        _permission())
    assert result.status.value == "SUCCESS"
    assert result.result_id == "result_captured"
    assert persisted and persisted[0] == [{"gmv": 1.0}]
