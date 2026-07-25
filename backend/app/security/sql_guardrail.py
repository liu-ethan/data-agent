"""Minimal deterministic SQL safety and role checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.schema import APP_TABLES, SENSITIVE_USER_COLUMNS

_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_SINGLE_QUOTED_QUALIFIER_RE = re.compile(r"'(?:''|[^'])*'(?=\s*\.)")
_QUOTED_IDENTIFIER_RE = re.compile(
    r"""(?:"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]+\])"""
)
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
_DDL_OR_DANGEROUS = (
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "ATTACH",
    "DETACH",
    "REPLACE",
    "PRAGMA",
)
_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE")
_WRITE_TABLE_RE = re.compile(
    rf"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?P<table>{_IDENTIFIER})"
    rf"(?:\s*\.\s*(?P<qualified>{_IDENTIFIER}))?",
    re.IGNORECASE,
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
            result.append(" ")
            index += 2
            while index < len(sql) and sql[index] != "\n":
                index += 1
            if index < len(sql):
                result.append("\n")
        elif char == "/" and next_char == "*":
            result.append(" ")
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


def _select_lists(sql: str) -> list[str]:
    """Return every SELECT list, including lists in nested queries."""
    select_lists: list[str] = []
    for select_match in re.finditer(r"\bSELECT\b", sql):
        depth = 0
        index = select_match.end()
        while index < len(sql):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0:
                from_match = re.match(r"\bFROM\b", sql[index:])
                if from_match:
                    select_lists.append(sql[select_match.end() : index])
                    break
            index += 1
    return select_lists


def check_sql(sql: str, *, user_role: str) -> GuardrailResult:
    """Check whether a single SQL statement is safe for the role."""
    if not sql.strip():
        return _reject("SQL must not be empty")
    if user_role not in {"analyst", "admin"}:
        return _reject("Unknown user role")

    sql_without_comments = _strip_comments(sql)
    sql_with_qualified_identifiers = _SINGLE_QUOTED_QUALIFIER_RE.sub(
        lambda match: _unquote_identifier(match.group()),
        sql_without_comments,
    )
    sql_without_strings = _STRING_LITERAL_RE.sub("''", sql_with_qualified_identifiers)
    if ";" in sql_without_strings:
        return _reject("Multiple SQL statements are not allowed")

    normalized = _QUOTED_IDENTIFIER_RE.sub(
        lambda match: _unquote_identifier(match.group()),
        sql_without_strings,
    ).upper()
    query_start = _LEADING_COMMENT_RE.sub("", normalized)
    blocked_tables = {table_name.upper() for table_name in APP_TABLES} | {
        "SQLITE_MASTER"
    }

    if user_role == "analyst":
        if not re.match(r"(?:SELECT|WITH)\b", query_start):
            return _reject("Only SELECT or WITH queries are allowed")
        for keyword in _DDL_OR_DANGEROUS + _WRITE_KEYWORDS:
            if re.search(rf"\b{keyword}\b", normalized):
                return _reject(f"{keyword} is not allowed")

        sources = _sources(sql_without_comments)
        for table_name, _ in sources:
            if table_name in blocked_tables:
                return _reject(f"Access to {table_name.lower()} is not allowed")

        if any(table_name == "USERS" for table_name, _ in sources):
            non_user_qualifiers = {
                qualifier
                for table_name, alias in sources
                if table_name != "USERS"
                for qualifier in (table_name, alias)
                if qualifier
            }
            sensitive_columns = "|".join(
                re.escape(column.upper()) for column in SENSITIVE_USER_COLUMNS
            )
            for columns in _select_lists(normalized):
                if re.search(r"(?<![\w.])\*(?!\w)", columns):
                    return _reject("Analysts cannot select all user columns")
                for wildcard in re.finditer(
                    r"(?<![\w.])(?P<qualifier>[A-Z_]\w*)\s*\.\s*\*",
                    columns,
                ):
                    if wildcard.group("qualifier") not in non_user_qualifiers:
                        return _reject("Analysts cannot select all user columns")
                for sensitive in re.finditer(
                    rf"(?<![\w.])(?:(?P<qualifier>[A-Z_]\w*)\s*\.\s*)?"
                    rf"(?:{sensitive_columns})\b",
                    columns,
                ):
                    qualifier = sensitive.group("qualifier")
                    if qualifier not in non_user_qualifiers:
                        return _reject("Analysts cannot access sensitive user columns")

        return GuardrailResult(ok=True, reason=None)

    if not re.match(r"(?:SELECT|WITH|INSERT|UPDATE|DELETE)\b", query_start):
        return _reject("Only SELECT/WITH/INSERT/UPDATE/DELETE are allowed")
    for keyword in _DDL_OR_DANGEROUS:
        if re.search(rf"\b{keyword}\b", normalized):
            return _reject(f"{keyword} is not allowed")

    sources = _sources(sql_without_comments)
    for table_name, _ in sources:
        if table_name in blocked_tables:
            return _reject(f"Access to {table_name.lower()} is not allowed")

    for match in _WRITE_TABLE_RE.finditer(sql_without_comments):
        table_name = _unquote_identifier(
            match.group("qualified") or match.group("table")
        )
        if table_name in blocked_tables:
            return _reject(f"Access to {table_name.lower()} is not allowed")

    return GuardrailResult(ok=True, reason=None)
