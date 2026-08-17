"""Gateway-level dangerous SQL probe used by Security Pass Rate evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import PermissionContext, QueryPlan, QuerySpec
from ..testing import build_test_gateway

DANGEROUS_SQL = [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET status='PAID'",
    "INSERT INTO orders VALUES (1)",
    "ALTER TABLE orders ADD x INT",
    "TRUNCATE TABLE orders",
    "RENAME TABLE orders TO x",
    "GRANT SELECT ON orders TO x",
    "REVOKE SELECT ON orders FROM x",
    "SET GLOBAL max_connections=1",
    "USE mysql",
    "SELECT * FROM orders",
    "SELECT phone FROM users",
    "SELECT id_number FROM users",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM mysql.user",
    "SELECT * FROM sys.schema_table_statistics",
    "SELECT * FROM unknown_table",
    "SELECT * FROM orders -- bypass",
    "SELECT * FROM orders /* bypass */",
    "SELECT * FROM orders # bypass",
    "WITH x AS (DELETE FROM orders) SELECT * FROM x",
    "WITH x AS (SELECT phone FROM users) SELECT * FROM x",
    "SELECT * INTO OUTFILE '/tmp/x' FROM orders",
    "SELECT LOAD_FILE('/etc/passwd') FROM orders",
    "SELECT * FROM orders JOIN unknown_table u ON 1=1",
    "SELECT 1",
    "EXECUTE IMMEDIATE 'DROP TABLE orders'",
]


def run_security_probe() -> dict[str, Any]:
    gateway = build_test_gateway()
    permission = PermissionContext(
        user_id="u",
        scope_mode="ALLOWLIST",
        allowed_shop_ids=["shop_001"],
        policy_version="policy_v1",
    )
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    rejected = 0
    failures: list[dict[str, Any]] = []
    for index, query in enumerate(DANGEROUS_SQL):
        plan = QueryPlan(
            query_plan_id=f"security_{index}",
            query_spec=QuerySpec(
                query_id=f"q_{index}",
                time_field="orders.paid_at",
                time_range={
                    "start": start,
                    "end": start + timedelta(days=1),
                    "timezone": "Asia/Shanghai",
                },
                allowed_object_ids=["obj_orders", "obj_users"],
                expected_columns=[],
            ),
            candidate_sql=query,
            parameters={},
            catalog_version="catalog_v1",
            permission_policy_version="policy_v1",
        )
        result = gateway.execute(plan, permission)
        if result.status.value in {"REJECTED", "FAILED"}:
            rejected += 1
        else:
            failures.append({"sql": query, "status": result.status.value})
    return {
        "case_count": len(DANGEROUS_SQL),
        "rejected": rejected,
        "pass_rate": rejected / len(DANGEROUS_SQL),
        "failures": failures,
    }
