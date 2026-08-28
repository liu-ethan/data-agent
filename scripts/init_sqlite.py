"""Initialize split SQLite control-plane databases. Never writes to MySQL."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "migrations" / "sqlite"
DATA_DIR = ROOT / "data" / "sqlite"

FILES = {
    "users": "users.sql",
    "catalog": "catalog.sql",
    "embeddings": "embeddings.sql",
    "checkpoint": "checkpoint.sql",
    "runtime": "runtime.sql",
    "results": "results.sql",
}

TENANT = "default"
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

SLICE_TABLES = [
    ("dim_store", "门店", "门店", "一行一个线下店或电商仓店"),
    ("dim_user", "用户", "用户", "一行一个买家"),
    ("dim_category", "品类", "商品", "一行一个品类"),
    ("dim_sku", "SKU", "商品", "一行一个可售 SKU"),
    ("dim_channel", "渠道", "营销", "一行一个成交/流量渠道"),
    ("dim_campaign", "营销活动", "营销", "一行一个投放活动"),
    ("fact_order", "订单", "订单", "一行一笔订单"),
    ("fact_order_item", "订单行", "订单", "一行一个 SKU 明细，GMV grain"),
    ("fact_payment", "支付", "支付", "一行一次支付尝试"),
    ("fact_refund", "退款", "退款", "一行一笔退款"),
    ("fact_traffic", "流量", "用户", "一行=某店某渠道某日访客"),
    ("fact_ad_spend", "广告花费", "营销", "一行=某活动某渠道某月花费"),
]

RELATIONS = [
    ("fact_order", "user_id", "dim_user", "id", "many_to_one", "fk"),
    ("fact_order", "store_id", "dim_store", "id", "many_to_one", "fk"),
    ("fact_order", "channel_id", "dim_channel", "id", "many_to_one", "fk"),
    ("fact_order", "campaign_id", "dim_campaign", "id", "many_to_one", "fk"),
    ("fact_order_item", "order_id", "fact_order", "id", "many_to_one", "fk"),
    ("fact_order_item", "sku_id", "dim_sku", "id", "many_to_one", "fk"),
    ("dim_sku", "category_id", "dim_category", "id", "many_to_one", "fk"),
    ("fact_payment", "order_id", "fact_order", "id", "many_to_one", "fk"),
    ("fact_refund", "order_id", "fact_order", "id", "many_to_one", "fk"),
    ("fact_refund", "order_item_id", "fact_order_item", "id", "many_to_one", "fk"),
    ("dim_campaign", "channel_id", "dim_channel", "id", "many_to_one", "fk"),
    ("fact_ad_spend", "campaign_id", "dim_campaign", "id", "many_to_one", "fk"),
    ("fact_ad_spend", "channel_id", "dim_channel", "id", "many_to_one", "fk"),
    ("fact_traffic", "store_id", "dim_store", "id", "many_to_one", "fk"),
    ("fact_traffic", "channel_id", "dim_channel", "id", "many_to_one", "fk"),
]

PAID_STATUSES = ["paid", "shipped", "completed"]

METRICS = [
    {
        "metric_id": "gmv",
        "name": "GMV",
        "version": 1,
        "grain_table": "fact_order_item",
        "formula": "SUM(oi.price * oi.qty)",
        "time_field": "fact_order.created_at",
        "unit": "CNY",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order_item.price", "fact_order_item.qty", "fact_order.status", "fact_order.created_at"],
        "needs_tables": [],
    },
    {
        "metric_id": "paid_gmv",
        "name": "实付GMV",
        "version": 1,
        "grain_table": "fact_order_item",
        "formula": "SUM(oi.pay_amt)",
        "time_field": "fact_order.paid_at",
        "unit": "CNY",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order_item.pay_amt", "fact_order.status", "fact_order.paid_at"],
        "needs_tables": [],
    },
    {
        "metric_id": "net_gmv",
        "name": "净GMV",
        "version": 1,
        "grain_table": "fact_order_item",
        "formula": "SUM(oi.pay_amt) - COALESCE(SUM(r.amount), 0)",
        "time_field": "fact_order.paid_at",
        "unit": "CNY",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order_item.pay_amt", "fact_refund.amount", "fact_order.status"],
        "needs_tables": [],
    },
    {
        "metric_id": "order_count",
        "name": "订单量",
        "version": 1,
        "grain_table": "fact_order",
        "formula": "COUNT(DISTINCT o.id)",
        "time_field": "fact_order.created_at",
        "unit": "count",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order.id", "fact_order.status", "fact_order.created_at"],
        "needs_tables": [],
    },
    {
        "metric_id": "aov",
        "name": "客单价",
        "version": 1,
        "grain_table": "fact_order",
        "formula": "SUM(oi.pay_amt) / NULLIF(COUNT(DISTINCT o.id), 0)",
        "time_field": "fact_order.paid_at",
        "unit": "CNY",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order_item.pay_amt", "fact_order.id"],
        "needs_tables": [],
    },
    {
        "metric_id": "refund_rate",
        "name": "退款率",
        "version": 1,
        "grain_table": "fact_refund",
        "formula": "SUM(r.amount) / NULLIF(SUM(oi.pay_amt), 0)",
        "time_field": "fact_refund.refunded_at",
        "unit": "ratio",
        "filters": [{"field": "fact_refund.status", "op": "=", "value": "success"}],
        "deps": ["fact_refund.amount", "fact_order_item.pay_amt"],
        "needs_tables": [],
    },
    {
        "metric_id": "cvr",
        "name": "转化率",
        "version": 1,
        "grain_table": "fact_order",
        "formula": "COUNT(DISTINCT o.user_id) / NULLIF(SUM(t.visitor_cnt), 0)",
        "time_field": "fact_traffic.dt",
        "unit": "ratio",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order.user_id", "fact_traffic.visitor_cnt"],
        "needs_tables": ["fact_traffic"],
    },
    {
        "metric_id": "new_customers",
        "name": "新客数",
        "version": 1,
        "grain_table": "fact_order",
        "formula": "COUNT(DISTINCT CASE WHEN u.first_order_at >= :start AND u.first_order_at < :end THEN o.user_id END)",
        "time_field": "dim_user.first_order_at",
        "unit": "count",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["dim_user.first_order_at", "fact_order.user_id"],
        "needs_tables": [],
    },
    {
        "metric_id": "repurchase_rate",
        "name": "复购率",
        "version": 1,
        "grain_table": "fact_order",
        "formula": "COUNT(DISTINCT CASE WHEN order_cnt >= 2 THEN user_id END) / NULLIF(COUNT(DISTINCT user_id), 0)",
        "time_field": "fact_order.created_at",
        "unit": "ratio",
        "filters": [{"field": "fact_order.status", "op": "in", "value": PAID_STATUSES}],
        "deps": ["fact_order.user_id", "fact_order.id"],
        "needs_tables": [],
    },
    {
        "metric_id": "ad_roi",
        "name": "广告ROI",
        "version": 1,
        "grain_table": "fact_order_item",
        "formula": "SUM(oi.pay_amt) / NULLIF(SUM(a.amount), 0)",
        "time_field": "fact_ad_spend.dt_month",
        "unit": "ratio",
        "filters": [{"field": "fact_order.campaign_id", "op": "!=", "value": None}],
        "deps": ["fact_order_item.pay_amt", "fact_ad_spend.amount", "fact_order.campaign_id"],
        "needs_tables": ["fact_ad_spend", "dim_campaign"],
    },
]

WRITE_OPS = [
    (
        "update_sku_status",
        "dim_sku",
        ["status"],
        "UPDATE dim_sku SET status = :status, row_version = row_version + 1 WHERE id IN :ids AND row_version = :row_version",
        100,
        1,
        "row_version",
    ),
    (
        "adjust_sku_inventory",
        "dim_sku",
        ["inventory_qty"],
        "UPDATE dim_sku SET inventory_qty = :inventory_qty, row_version = row_version + 1 WHERE id IN :ids AND row_version = :row_version",
        100,
        1,
        "row_version",
    ),
]

ALL_TABLES = [t[0] for t in SLICE_TABLES]
ALL_METRICS = [m["metric_id"] for m in METRICS]


def password_hash(password: str, salt: str = "local-dev-salt") -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"{salt}${digest.hex()}"


def apply_sql(db_path: Path, sql_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = sql_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()


def seed_users(db_path: Path) -> None:
    users = [
        ("u-admin", "admin", "admin", "本地管理员", "operator"),
        ("u-analyst", "analyst", "analyst", "分析师", "analyst"),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM user_permission")
        conn.execute("DELETE FROM app_user")
        for user_id, username, password, display_name, role in users:
            conn.execute(
                "INSERT INTO app_user VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (user_id, username, password_hash(password), display_name, role, TENANT, NOW),
            )
            write_ops = ["update_sku_status", "adjust_sku_inventory"] if role == "operator" else []
            conn.execute(
                "INSERT INTO user_permission VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    json.dumps(ALL_TABLES),
                    json.dumps([f"data-agent-ecommerce.{t}.*" for t in ALL_TABLES]),
                    json.dumps(ALL_METRICS),
                    json.dumps(write_ops),
                    NOW,
                ),
            )
        conn.commit()


def seed_catalog(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM write_op")
        conn.execute("DELETE FROM metric_spec")
        conn.execute("DELETE FROM schema_relation")
        conn.execute("DELETE FROM schema_column")
        conn.execute("DELETE FROM schema_table")
        conn.execute("DELETE FROM catalog_meta")
        conn.execute(
            "INSERT INTO catalog_meta VALUES (1, ?, ?, ?)",
            ("data-agent-ecommerce", NOW, "dev_slice seed"),
        )
        for table_name, business_name, domain, grain in SLICE_TABLES:
            conn.execute(
                "INSERT INTO schema_table VALUES (?, ?, ?, ?, ?, '[]')",
                (table_name, business_name, domain, grain, grain),
            )
        for i, rel in enumerate(RELATIONS, start=1):
            conn.execute(
                "INSERT INTO schema_relation VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)",
                (i, *rel),
            )
        for metric in METRICS:
            conn.execute(
                """INSERT INTO metric_spec
                   (metric_id, name, version, grain_table, formula, time_field, unit,
                    filters_json, deps_json, needs_tables_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metric["metric_id"],
                    metric["name"],
                    metric["version"],
                    metric["grain_table"],
                    metric["formula"],
                    metric["time_field"],
                    metric["unit"],
                    json.dumps(metric["filters"], ensure_ascii=False),
                    json.dumps(metric["deps"]),
                    json.dumps(metric["needs_tables"]),
                ),
            )
        for op in WRITE_OPS:
            conn.execute(
                "INSERT INTO write_op VALUES (?, ?, ?, ?, ?, ?, ?)",
                (op[0], op[1], json.dumps(op[2]), op[3], op[4], op[5], op[6]),
            )
        conn.commit()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "results").mkdir(parents=True, exist_ok=True)
    for name, sql_file in FILES.items():
        db_path = DATA_DIR / f"{name}.sqlite"
        apply_sql(db_path, SQL_DIR / sql_file)
        print(f"init {db_path}")
    seed_users(DATA_DIR / "users.sqlite")
    seed_catalog(DATA_DIR / "catalog.sqlite")
    print("seeded users.sqlite and catalog.sqlite")


if __name__ == "__main__":
    main()
