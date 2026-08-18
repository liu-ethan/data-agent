"""SQLite and in-memory adapters for deterministic tests only."""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
from typing import Any
from uuid import uuid4


class ResultRepository:
    def __init__(self) -> None:
        self._results: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def save(self, rows: list[dict[str, Any]], *, owner_user_id: str | None = None) -> str:
        result_id = f"result_{uuid4().hex[:12]}"
        with self._lock:
            self._results[result_id] = [dict(row) for row in rows]
        return result_id

    def get(self, result_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            rows = self._results.get(result_id)
            return [dict(row) for row in rows] if rows is not None else None

    def page(self, result_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        rows = self.get(result_id)
        if rows is None:
            raise KeyError(result_id)
        return {"result_id": result_id, "rows": rows[offset:offset + limit],
                "offset": offset, "limit": limit, "total": len(rows)}

    def csv(self, result_id: str) -> str:
        rows = self.get(result_id)
        if rows is None:
            raise KeyError(result_id)
        output = io.StringIO()
        if not rows:
            return ""
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()


class SQLiteDataRepository:
    """A local, deterministic substitute for the configured MySQL reader."""

    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        self.seed()

    def _create_schema(self) -> None:
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS shops(shop_id TEXT PRIMARY KEY, shop_name TEXT, region_code TEXT, region_name TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, phone TEXT, id_number TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS categories(category_id TEXT PRIMARY KEY, parent_id TEXT, category_name TEXT);
        CREATE TABLE IF NOT EXISTS products(product_id TEXT PRIMARY KEY, shop_id TEXT, category_id TEXT, product_name TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, user_id TEXT, shop_id TEXT, status TEXT, paid_at TEXT, pay_amount REAL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items(item_id TEXT PRIMARY KEY, order_id TEXT, shop_id TEXT, product_id TEXT, quantity INTEGER, item_paid_amount REAL);
        CREATE TABLE IF NOT EXISTS refunds(refund_id TEXT PRIMARY KEY, order_id TEXT, shop_id TEXT, status TEXT, refund_amount REAL, refunded_at TEXT);
        CREATE TABLE IF NOT EXISTS refund_items(refund_item_id TEXT PRIMARY KEY, refund_id TEXT, shop_id TEXT, order_item_id TEXT, refund_amount REAL);
        """)

    def seed(self) -> None:
        # Order dates are anchored to the runtime clock so the
        # deterministic eval suite (which queries "昨天") keeps matching
        # whichever calendar day the seed runs on. Matches the MySQL seed
        # in scripts/mock_mysql_data.sh.
        from datetime import datetime, timedelta
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        three_days_ago = today - timedelta(days=3)
        seven_days_ago = today - timedelta(days=7)

        rows = {
            "shops": [("shop_001", "东城数码旗舰店", "CN-EAST", "华东", "ACTIVE"),
                      ("shop_002", "华南生活馆", "CN-SOUTH", "华南地区", "ACTIVE"),
                      ("shop_003", "西部家居店", "CN-WEST", "西部", "INACTIVE")],
            "users": [("user_001", "13800000001", "110101199001011234", "2026-07-01 09:00:00"),
                      ("user_002", "13800000002", "310101199202022345", "2026-07-03 10:30:00"),
                      ("user_003", "13900000003", "440101198803033456", "2026-07-05 14:20:00"),
                      ("user_004", "13900000004", "510101199504044567", "2026-07-08 16:45:00")],
            "categories": [("cat_110", "cat_100", "手机通讯"), ("cat_120", "cat_100", "电脑周边"),
                            ("cat_210", "cat_200", "厨房用品"), ("cat_310", "cat_300", "护肤")],
            "products": [("prod_1001", "shop_001", "cat_110", "智能手机", "ACTIVE"),
                         ("prod_1002", "shop_001", "cat_110", "手机保护壳", "ACTIVE"),
                         ("prod_1003", "shop_001", "cat_120", "无线耳机", "ACTIVE"),
                         ("prod_1004", "shop_001", "cat_120", "快充充电宝", "ACTIVE"),
                         ("prod_2001", "shop_002", "cat_210", "铸铁锅", "ACTIVE"),
                         ("prod_2002", "shop_002", "cat_210", "厨房收纳盒", "ACTIVE"),
                         ("prod_3001", "shop_002", "cat_310", "保湿面霜", "ACTIVE"),
                         ("prod_3002", "shop_002", "cat_310", "修护乳霜", "ACTIVE")],
            "orders": [("ord_001", "user_001", "shop_001", "PAID", yesterday.strftime("%Y-%m-%d ") + "09:10:00", 1299,
 yesterday.strftime("%Y-%m-%d ") + "08:30:00"),
                       ("ord_002", "user_002", "shop_001", "PAID", two_days_ago.strftime("%Y-%m-%d ") + "11:20:00", 500,
 two_days_ago.strftime("%Y-%m-%d ") + "10:30:00"),
                       ("ord_003", "user_003", "shop_002", "PAID", yesterday.strftime("%Y-%m-%d ") + "13:05:00", 780,
 yesterday.strftime("%Y-%m-%d ") + "11:30:00"),
                       ("ord_004", "user_001", "shop_002", "PAID", three_days_ago.strftime("%Y-%m-%d ") + "14:10:00", 320,
 three_days_ago.strftime("%Y-%m-%d ") + "13:00:00"),
                       ("ord_005", "user_004", "shop_001", "PAID", seven_days_ago.strftime("%Y-%m-%d ") + "10:00:00", 899,
 seven_days_ago.strftime("%Y-%m-%d ") + "09:00:00"),
                       ("ord_007", "user_004", "shop_002", "UNPAID", None, 560, "2026-08-11 09:00:00"),
                       ("ord_008", "user_003", "shop_001", "PAYMENT_FAILED", None, 199, "2026-08-12 09:00:00"),
                       ("ord_009", "user_002", "shop_002", "CANCELLED", None, 560, "2026-08-13 09:00:00"),
                       ("ord_010", "user_001", "shop_001", "PAID", two_days_ago.strftime("%Y-%m-%d ") + "08:00:00", 700,
 two_days_ago.strftime("%Y-%m-%d ") + "07:30:00")],
            "order_items": [("item_001", "ord_001", "shop_001", "prod_1001", 1, 899),
                             ("item_002", "ord_001", "shop_001", "prod_1002", 1, 49),
                             ("item_003", "ord_001", "shop_001", "prod_1003", 1, 351),
                             ("item_004", "ord_002", "shop_001", "prod_1003", 1, 351),
                             ("item_005", "ord_002", "shop_001", "prod_1002", 1, 49),
                             ("item_006", "ord_002", "shop_001", "prod_1004", 1, 100),
                             ("item_007", "ord_003", "shop_002", "prod_2001", 1, 500),
                             ("item_008", "ord_003", "shop_002", "prod_2002", 1, 280),
                             ("item_009", "ord_004", "shop_002", "prod_3001", 1, 180),
                             ("item_010", "ord_004", "shop_002", "prod_3002", 1, 140),
                             ("item_011", "ord_005", "shop_001", "prod_1001", 1, 899),
                             ("item_012", "ord_010", "shop_001", "prod_1004", 1, 700)],
            "refunds": [("refund_001", "ord_001", "shop_001", "SUCCESS", 100,
 yesterday.strftime("%Y-%m-%d ") + "18:00:00"),
                        ("refund_002", "ord_001", "shop_001", "SUCCESS", 50,
 two_days_ago.strftime("%Y-%m-%d ") + "09:00:00"),
                        ("refund_003", "ord_003", "shop_002", "PENDING", 280, None),
                        ("refund_004", "ord_002", "shop_001", "FAILED", 49,
 yesterday.strftime("%Y-%m-%d ") + "12:00:00")],
            "refund_items": [("refund_item_001", "refund_001", "shop_001", "item_001", 100),
                              ("refund_item_002", "refund_002", "shop_001", "item_003", 50),
                              ("refund_item_003", "refund_003", "shop_002", "item_008", 280),
                              ("refund_item_004", "refund_004", "shop_001", "item_005", 49)],
        }
        columns = {"shops": "shop_id,shop_name,region_code,region_name,status", "users": "user_id,phone,id_number,created_at",
                   "categories": "category_id,parent_id,category_name", "products": "product_id,shop_id,category_id,product_name,status",
                   "orders": "order_id,user_id,shop_id,status,paid_at,pay_amount,created_at", "order_items": "item_id,order_id,shop_id,product_id,quantity,item_paid_amount",
                   "refunds": "refund_id,order_id,shop_id,status,refund_amount,refunded_at", "refund_items": "refund_item_id,refund_id,shop_id,order_item_id,refund_amount"}
        for table, values in rows.items():
            placeholders = ",".join("?" for _ in values[0])
            self.connection.executemany(f"INSERT OR REPLACE INTO {table} ({columns[table]}) VALUES ({placeholders})", values)
        self.connection.commit()

    def explain(self, sql: str, params: dict[str, Any]) -> tuple[float, int]:
        try:
            plan = self.connection.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            scans = sum(1 for row in plan if "SCAN" in str(row).upper())
            return float(len(plan) + scans * 10), len(plan)
        except sqlite3.Error as exc:
            raise ValueError(str(exc)) from exc

    def fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cursor = self.connection.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def writer_identity(self) -> str:
        return "agent_writer"

    def fetch_target(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        return self.fetch(sql, parameters)

    def apply_update(self, table: str, filters: dict[str, Any], changes: dict[str, Any]) -> int:
        assignments = ", ".join(f"{field} = :{field}" for field in changes)
        key = next(iter(filters))
        sql = f"UPDATE {table} SET {assignments} WHERE {key} = :{key}"
        cursor = self.connection.execute(sql, {**changes, **filters})
        self.connection.commit()
        return int(cursor.rowcount)

    def execute_write(self, sql: str, parameters: dict[str, Any]) -> int:
        cursor = self.connection.execute(sql, parameters)
        self.connection.commit()
        return int(cursor.rowcount)
