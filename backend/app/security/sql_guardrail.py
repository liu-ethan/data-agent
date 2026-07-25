"""Minimal deterministic SQL safety and role checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.schema import APP_TABLES, SENSITIVE_USER_COLUMNS

_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_LEADING_COMMENT_RE = re.compile(r"\A(?:\s*--[^\n]*(?:\n|\Z))*\s*")
_FORBIDDEN_KEYWORDS = (
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "ATTACH",
    "DETACH",
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "PRAGMA",
)


@dataclass(frozen=True)
class GuardrailResult:
    ok: bool
    reason: str | None


def _reject(reason: str) -> GuardrailResult:
    return GuardrailResult(ok=False, reason=reason)


def check_sql(sql: str, *, user_role: str) -> GuardrailResult:
    """Check whether a single read-only SQL query is safe for the role."""
    if not sql.strip():
        return _reject("SQL must not be empty")

    sql_without_strings = _STRING_LITERAL_RE.sub("''", sql)
    if ";" in sql_without_strings:
        return _reject("Multiple SQL statements are not allowed")

    normalized = sql_without_strings.upper()
    query_start = _LEADING_COMMENT_RE.sub("", normalized)
    if not re.match(r"(?:SELECT|WITH)\b", query_start):
        return _reject("Only SELECT or WITH queries are allowed")

    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", normalized):
            return _reject(f"{keyword} is not allowed")

    blocked_tables = APP_TABLES | {"sqlite_master"}
    for table_name in blocked_tables:
        if re.search(rf"\b{re.escape(table_name.upper())}\b", normalized):
            return _reject(f"Access to {table_name} is not allowed")

    if user_role == "analyst":
        sensitive_columns = "|".join(
            re.escape(column.upper()) for column in SENSITIVE_USER_COLUMNS
        )
        if re.search(
            rf"\bUSERS\s*\.\s*(?:{sensitive_columns})\b",
            normalized,
        ):
            return _reject("Analysts cannot access sensitive user columns")

        users_in_source = re.search(r"\b(?:FROM|JOIN)\s+USERS\b", normalized)
        select_list = re.search(r"\bSELECT\b(?P<columns>.*?)\bFROM\b", normalized, re.DOTALL)
        if users_in_source and select_list:
            columns = select_list.group("columns")
            if re.search(rf"(?<![\w.])(?:{sensitive_columns})\b", columns):
                return _reject("Analysts cannot access sensitive user columns")

    return GuardrailResult(ok=True, reason=None)
