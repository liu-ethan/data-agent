from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import PermissionContext, QueryPlan, QuerySpec
from backend.app.testing import build_test_gateway


DANGEROUS_SQL = [
    "DROP TABLE orders", "DELETE FROM orders", "UPDATE orders SET status='PAID'", "INSERT INTO orders VALUES (1)",
    "ALTER TABLE orders ADD x INT", "TRUNCATE TABLE orders", "RENAME TABLE orders TO x", "GRANT SELECT ON orders TO x",
    "REVOKE SELECT ON orders FROM x", "SET GLOBAL max_connections=1", "USE mysql", "SELECT * FROM orders",
    "SELECT phone FROM users", "SELECT id_number FROM users", "SELECT 1; SELECT 2", "SELECT 1; DROP TABLE orders",
    "SELECT * FROM information_schema.tables", "SELECT * FROM mysql.user", "SELECT * FROM sys.schema_table_statistics",
    "SELECT * FROM unknown_table", "SELECT * FROM orders -- bypass", "SELECT * FROM orders /* bypass */",
    "SELECT * FROM orders # bypass", "WITH x AS (DELETE FROM orders) SELECT * FROM x",
    "WITH x AS (SELECT phone FROM users) SELECT * FROM x", "SELECT * INTO OUTFILE '/tmp/x' FROM orders",
    "SELECT LOAD_FILE('/etc/passwd') FROM orders", "SELECT * FROM orders JOIN unknown_table u ON 1=1",
    "SELECT 1", "EXECUTE IMMEDIATE 'DROP TABLE orders'",
]


def test_thirty_dangerous_queries_are_rejected():
    assert len(DANGEROUS_SQL) == 30
    gateway = build_test_gateway()
    permission = PermissionContext(user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_001"], policy_version="policy_v1")
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    for index, sql in enumerate(DANGEROUS_SQL):
        plan = QueryPlan(query_plan_id=f"danger_{index}", query_spec=QuerySpec(query_id=f"q_{index}",
            time_field="orders.paid_at", time_range={"start": start, "end": start + timedelta(days=1), "timezone":"Asia/Shanghai"},
            allowed_object_ids=["obj_orders", "obj_users"], expected_columns=[]), candidate_sql=sql,
            parameters={}, catalog_version="catalog_v1", permission_policy_version="policy_v1")
        result = gateway.execute(plan, permission)
        assert result.status.value in {"REJECTED", "FAILED"}, (index, sql, result.model_dump())
        assert result.error_code in {"SQL_FORBIDDEN_OPERATION", "SQL_PARSE_ERROR", "SQL_OBJECT_NOT_ALLOWED", "QUERY_SPEC_MISMATCH"}, (index, result.model_dump())
