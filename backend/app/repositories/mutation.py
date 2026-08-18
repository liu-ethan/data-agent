"""Production MySQL writer adapter used only by WriteGateway."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from typing import Any

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from ..errors import RuntimeAgentError

_FORBIDDEN_WRITER_PRIVILEGES = {
    "ALL PRIVILEGES", "ALTER", "CREATE", "DELETE", "DROP", "FILE", "GRANT OPTION",
    "INDEX", "INSERT", "LOCK TABLES", "REFERENCES", "RELOAD", "SHUTDOWN", "TRIGGER",
}
_ALLOWED_TABLE = "products"
_ALLOWED_UPDATE_COLUMN = "product_name"


class MySQLMutationRepository:
    """Column-scoped writer used by ``WriteGateway`` in production."""

    def __init__(self, mysql: dict[str, Any]) -> None:
        account = mysql.get("accounts", {}).get("writer", {})
        username, password = account.get("username"), account.get("password")
        business_database = mysql.get("business_database") or mysql.get("database")
        if not all((mysql.get("host"), business_database, username, password)):
            raise RuntimeError("MySQL writer account is not fully configured")
        if str(username) == mysql.get("accounts", {}).get("migration", {}).get("username"):
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "migration account cannot be used on the write path",
            )
        url = URL.create(
            "mysql+pymysql",
            username=username,
            password=password,
            host=mysql["host"],
            port=int(mysql.get("port", 3306)),
            database=business_database,
            query={"charset": mysql.get("charset", "utf8mb4")},
        )
        self.engine = create_engine(
            url,
            future=True,
            pool_pre_ping=True,
            pool_size=int(mysql.get("pool_size", 5)),
            max_overflow=int(mysql.get("max_overflow", 5)),
            pool_recycle=int(mysql.get("pool_recycle_seconds", 1800)),
        )
        self.configured_username = str(username)
        self._verified = False
        self._verification_lock = threading.Lock()

    def writer_identity(self) -> str:
        with self._write_connection() as connection:
            current_user = str(connection.execute(text("SELECT CURRENT_USER()")).scalar_one())
        return current_user

    def fetch_target(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        with self._write_connection() as connection:
            result = connection.execute(text(sql), parameters)
            return [dict(row) for row in result.mappings().all()]

    def execute_write(self, sql: str, parameters: dict[str, Any]) -> int:
        try:
            with self.engine.begin() as connection:
                self._ensure_verified(connection)
                result = connection.execute(text(sql), parameters)
                return int(result.rowcount or 0)
        except RuntimeAgentError:
            raise
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "MUTATION_EXECUTION_FAILED",
                "The database could not execute the write",
            ) from exc

    def _ensure_verified(self, connection: Connection) -> None:
        with self._verification_lock:
            verified = self._verified
        if verified:
            return
        self._verify_connection(connection)
        with self._verification_lock:
            self._verified = True

    @contextmanager
    def _write_connection(self):
        with self.engine.connect() as connection:
            self._ensure_verified(connection)
            yield connection

    def _verify_connection(self, connection: Connection) -> None:
        current_user = str(connection.execute(text("SELECT CURRENT_USER()")).scalar_one())
        authenticated_username = current_user.split("@", 1)[0]
        if authenticated_username != self.configured_username:
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "The write connection did not authenticate as the configured writer",
            )
        if authenticated_username == "agent_migration":
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "migration account cannot be used on the write path",
            )
        grants = [str(row[0]) for row in connection.execute(text("SHOW GRANTS FOR CURRENT_USER()"))]
        granted_privileges: set[str] = set()
        selected_tables: set[str] = set()
        update_columns: set[str] = set()
        for grant in grants:
            match = re.match(r"GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+", grant, flags=re.IGNORECASE)
            if not match:
                continue
            privileges = {item.strip().upper() for item in match.group(1).split(",")}
            scope = match.group(2).replace("`", "").strip().lower()
            table = scope.rsplit(".", 1)[-1]
            for privilege in privileges:
                column_match = re.match(r"UPDATE\s*\((.+)\)", privilege, flags=re.IGNORECASE)
                if column_match:
                    update_columns.update(
                        item.strip().lower().strip("`") for item in column_match.group(1).split(",")
                    )
                    granted_privileges.add("UPDATE")
                    continue
                granted_privileges.add(privilege)
            if "SELECT" in privileges:
                if scope == "*.*" or scope.endswith(".*"):
                    raise RuntimeAgentError(
                        "WRITER_ACCOUNT_OVERPRIVILEGED",
                        "The writer account must not have database-wide access",
                    )
                selected_tables.add(table)
        forbidden = sorted(granted_privileges & _FORBIDDEN_WRITER_PRIVILEGES)
        if forbidden:
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_OVERPRIVILEGED",
                "The configured writer account has unapproved privileges",
                details={"forbidden_privileges": forbidden},
            )
        if selected_tables != {_ALLOWED_TABLE} or update_columns != {_ALLOWED_UPDATE_COLUMN}:
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "The writer account must be limited to products.product_name",
            )
