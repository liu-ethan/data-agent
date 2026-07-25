"""Minimal deterministic SQL safety and role checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.schema import APP_TABLES, SENSITIVE_USER_COLUMNS

_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_LEADING_COMMENT_RE = re.compile(r"\A(?:\s*--[^\n]*(?:\n|\Z))*\s*")
_IDENTIFIER = r"""(?:"(?:""|[^"])*"|'(?:''|[^'])*'|`(?:``|[^`])*`|\[[^\]]+\]|[A-Z_]\w*)"""
_SOURCE_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+(?P<table>{_IDENTIFIER})"
    rf"(?:\s*\.\s*(?P<qualified>{_IDENTIFIER}))?"
    rf"(?:\s+(?:AS\s+)?(?P<alias>{_IDENTIFIER}))?",
    re.IGNORECASE,
)
_ALIAS_STOP_WORDS = {
    "CROSS",
    "FULL",
    "GROUP",
    "INNER",
    "JOIN",
    "LEFT",
    "LIMIT",
    "ON",
    "ORDER",
    "RIGHT",
    "UNION",
    "WHERE",
}
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


def _strip_comments(sql: str) -> str:
    """Remove SQL comments while leaving quoted content unchanged."""
    result: list[str] = []
    index = 0
    quote_end: str | None = None

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if quote_end:
            result.append(char)
            if char == quote_end:
                if quote_end != "]" and next_char == quote_end:
                    result.append(next_char)
                    index += 1
                else:
                    quote_end = None
        elif char in {"'", '"', "`", "["}:
            quote_end = "]" if char == "[" else char
            result.append(char)
        elif char == "-" and next_char == "-":
            index += 2
            while index < len(sql) and sql[index] != "\n":
                index += 1
            if index < len(sql):
                result.append("\n")
        elif char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                index += 1
            index += 1
        else:
            result.append(char)

        index += 1

    return "".join(result)


def _unquote_identifier(identifier: str) -> str:
    if identifier[0] == "[":
        return identifier[1:-1].upper()
    if identifier[0] in {"'", '"', "`"}:
        quote = identifier[0]
        return identifier[1:-1].replace(quote * 2, quote).upper()
    return identifier.upper()


def _sources(sql: str) -> list[tuple[str, str | None]]:
    sources: list[tuple[str, str | None]] = []
    for match in _SOURCE_RE.finditer(sql):
        table = _unquote_identifier(match.group("qualified") or match.group("table"))
        raw_alias = match.group("alias")
        alias = _unquote_identifier(raw_alias) if raw_alias else None
        if alias in _ALIAS_STOP_WORDS:
            alias = None
        sources.append((table, alias))
    return sources


def check_sql(sql: str, *, user_role: str) -> GuardrailResult:
    """Check whether a single read-only SQL query is safe for the role."""
    if not sql.strip():
        return _reject("SQL must not be empty")
    if user_role not in {"analyst", "admin"}:
        return _reject("Unknown user role")

    sql_without_comments = _strip_comments(sql)
    sql_without_strings = _STRING_LITERAL_RE.sub("''", sql_without_comments)
    if ";" in sql_without_strings:
        return _reject("Multiple SQL statements are not allowed")

    normalized = sql_without_strings.upper()
    query_start = _LEADING_COMMENT_RE.sub("", normalized)
    if not re.match(r"(?:SELECT|WITH)\b", query_start):
        return _reject("Only SELECT or WITH queries are allowed")

    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", normalized):
            return _reject(f"{keyword} is not allowed")

    sources = _sources(sql_without_comments)
    blocked_tables = {table_name.upper() for table_name in APP_TABLES} | {
        "SQLITE_MASTER"
    }
    for table_name, _ in sources:
        if table_name in blocked_tables:
            return _reject(f"Access to {table_name.lower()} is not allowed")

    if user_role == "analyst":
        sensitive_columns = "|".join(
            re.escape(column.upper()) for column in SENSITIVE_USER_COLUMNS
        )
        user_qualifiers = {
            alias or table_name
            for table_name, alias in sources
            if table_name == "USERS"
        }
        select_list = re.search(r"\bSELECT\b(?P<columns>.*?)\bFROM\b", normalized, re.DOTALL)
        if user_qualifiers and select_list:
            columns = select_list.group("columns")
            if re.search(r"(?<![\w.])\*(?!\w)", columns):
                return _reject("Analysts cannot select all user columns")
            for qualifier in user_qualifiers | {"USERS"}:
                if re.search(
                    rf"\b{re.escape(qualifier)}\s*\.\s*(?:\*|{sensitive_columns}\b)",
                    columns,
                ):
                    return _reject("Analysts cannot access sensitive user columns")
            if re.search(rf"(?<![\w.])(?:{sensitive_columns})\b", columns):
                return _reject("Analysts cannot access sensitive user columns")

    return GuardrailResult(ok=True, reason=None)
