"""Production MySQL analytical data adapter."""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from typing import Any

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from ..errors import RuntimeAgentError

_FORBIDDEN_READER_PRIVILEGES = {
    "ALL PRIVILEGES", "ALTER", "CREATE", "DELETE", "DROP", "FILE", "GRANT OPTION",
    "INDEX", "INSERT", "LOCK TABLES", "REFERENCES", "RELOAD", "SHUTDOWN",
    "TRIGGER", "UPDATE",
}

_REQUIRED_BUSINESS_COLUMNS = {
    "shops": {"shop_id", "shop_name", "region_code", "region_name", "status"},
    "users": {"user_id", "phone", "id_number", "created_at"},
    "categories": {"category_id", "parent_id", "category_name"},
    "products": {"product_id", "shop_id", "category_id", "product_name", "status"},
    "orders": {"order_id", "user_id", "shop_id", "status", "paid_at", "pay_amount", "created_at"},
    "order_items": {"item_id", "order_id", "shop_id", "product_id", "quantity", "item_paid_amount"},
    "refunds": {"refund_id", "order_id", "shop_id", "status", "refund_amount", "refunded_at"},
    "refund_items": {"refund_item_id", "refund_id", "shop_id", "order_item_id", "refund_amount"},
}


def _mysql_error_code(exc: DBAPIError) -> int | None:
    args = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


class MySQLDataRepository:
    """Read-only MySQL adapter used by ``ReadGateway`` in production.

    The engine is created with the dedicated reader account; the graph and LLM
    only ever receive the gateway interface, never this connection metadata.
    """

    def __init__(self, mysql: dict[str, Any], *, max_execution_ms: int = 5000) -> None:
        account = mysql.get("accounts", {}).get("reader", {})
        username, password = account.get("username"), account.get("password")
        business_database = mysql.get("business_database") or mysql.get("database")
        if not all((mysql.get("host"), business_database, username, password)):
            raise RuntimeError("MySQL reader account is not fully configured")
        url = URL.create("mysql+pymysql", username=username, password=password,
                         host=mysql["host"], port=int(mysql.get("port", 3306)),
                         database=business_database, query={"charset": mysql.get("charset", "utf8mb4")})
        self.engine = create_engine(url, future=True, pool_pre_ping=True,
                                    pool_size=int(mysql.get("pool_size", 10)),
                                    max_overflow=int(mysql.get("max_overflow", 20)),
                                    pool_recycle=int(mysql.get("pool_recycle_seconds", 1800)))
        self.configured_username = str(username)
        self.business_database = str(business_database)
        self.max_execution_ms = max_execution_ms
        self._verified = False
        self._verification_lock = threading.Lock()

    def _verify_connection(self, connection: Connection) -> dict[str, Any]:
        current_user = str(connection.execute(text("SELECT CURRENT_USER()")).scalar_one())
        authenticated_username = current_user.split("@", 1)[0]
        if authenticated_username != self.configured_username:
            raise RuntimeAgentError(
                "READER_ACCOUNT_INVALID",
                "The analytical connection did not authenticate as the configured reader",
            )
        grants = [str(row[0]) for row in connection.execute(
            text("SHOW GRANTS FOR CURRENT_USER()"))]
        granted_privileges: set[str] = set()
        selected_tables: set[str] = set()
        for grant in grants:
            match = re.match(r"GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+", grant,
                             flags=re.IGNORECASE)
            if match:
                privileges = {
                    item.strip().upper() for item in match.group(1).split(",")}
                granted_privileges.update(privileges)
                scope = match.group(2).replace("`", "").strip().lower()
                if "SELECT" in privileges:
                    if scope == "*.*" or scope.endswith(".*"):
                        raise RuntimeAgentError(
                            "READER_ACCOUNT_OVERPRIVILEGED",
                            "The reader account must not have database-wide SELECT access",
                        )
                    table = scope.rsplit(".", 1)[-1]
                    if table not in _REQUIRED_BUSINESS_COLUMNS:
                        raise RuntimeAgentError(
                            "READER_ACCOUNT_OVERPRIVILEGED",
                            "The reader account can only access approved analytical tables",
                        )
                    selected_tables.add(table)
        forbidden = sorted(granted_privileges & _FORBIDDEN_READER_PRIVILEGES)
        if forbidden:
            raise RuntimeAgentError(
                "READER_ACCOUNT_NOT_READ_ONLY",
                "The configured reader account has write or administrative privileges",
                details={"forbidden_privileges": forbidden},
            )
        missing_tables = sorted(set(_REQUIRED_BUSINESS_COLUMNS) - selected_tables)
        if "SELECT" not in granted_privileges or missing_tables:
            raise RuntimeAgentError(
                "READER_ACCOUNT_INVALID",
                "The configured reader account is missing required table-level SELECT grants",
                details={"missing_tables": missing_tables},
            )
        return {
            "current_user": current_user,
            "configured_username": self.configured_username,
            "read_only_grants": True,
            "authorized_tables": sorted(selected_tables),
        }

    def verify_reader_account(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            result = self._verify_connection(connection)
            connection.rollback()
        with self._verification_lock:
            self._verified = True
        return result

    @contextmanager
    def _read_connection(self):
        with self.engine.connect() as connection:
            with self._verification_lock:
                verified = self._verified
            if not verified:
                self._verify_connection(connection)
                with self._verification_lock:
                    self._verified = True
                connection.commit()
            connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            connection.execute(
                text("SET SESSION MAX_EXECUTION_TIME = :timeout"),
                {"timeout": self.max_execution_ms},
            )
            connection.commit()
            with connection.begin():
                yield connection

    def healthcheck(self) -> bool:
        with self._read_connection() as connection:
            read_only = connection.execute(
                text("SELECT @@session.transaction_read_only")).scalar_one()
            if int(read_only) != 1:
                raise RuntimeAgentError(
                    "READER_ACCOUNT_NOT_READ_ONLY",
                    "The analytical session is not in read-only transaction mode",
                )
            self._validate_schema(connection)
        return True

    def _validate_schema(self, connection: Connection) -> None:
        rows = connection.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE()"
        ))
        actual: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            actual.setdefault(str(table_name), set()).add(str(column_name))
        missing = [
            f"{table}.{column}"
            for table, required_columns in _REQUIRED_BUSINESS_COLUMNS.items()
            for column in sorted(required_columns - actual.get(table, set()))
        ]
        if missing:
            raise RuntimeAgentError(
                "DATA_SCHEMA_MISMATCH",
                "The configured MySQL schema does not satisfy the business data contract",
                details={"missing": missing},
            )

    def explain(self, sql: str, parameters: dict[str, Any]) -> tuple[float, int]:
        """Read EXPLAIN JSON conservatively; unknown fields never bypass limits."""
        try:
            with self._read_connection() as connection:
                raw = connection.execute(
                    text("EXPLAIN FORMAT=JSON " + sql), parameters).scalar()
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "EXPLAIN_FAILED", "The database could not explain the query") from exc
        try:
            document = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return float("inf"), 2**63 - 1

        costs: list[float] = []
        rows: list[int] = []
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                cost = value.get("query_cost")
                if isinstance(cost, (int, float, str)):
                    try: costs.append(float(cost))
                    except ValueError: pass
                estimate = value.get("rows_examined_per_scan", value.get("rows_produced_per_join"))
                if isinstance(estimate, (int, float, str)):
                    try: rows.append(int(float(estimate)))
                    except ValueError: pass
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(document)
        return (max(costs, default=0.0), sum(rows, start=0))

    def fetch(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            with self._read_connection() as connection:
                result = connection.execute(text(sql), parameters)
                return [dict(row) for row in result.mappings().all()]
        except DBAPIError as exc:
            if _mysql_error_code(exc) in {1205, 1317, 3024}:
                raise RuntimeAgentError(
                    "QUERY_TIMEOUT", "The read query exceeded its execution budget",
                    retryable=True,
                ) from exc
            raise RuntimeAgentError(
                "QUERY_EXECUTION_FAILED", "The database could not execute the read query",
                retryable=True,
            ) from exc
