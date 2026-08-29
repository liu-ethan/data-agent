from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app.catalog.models import CatalogSnapshot, TableRelation
from backend.app.gateway.explain import estimate_explain_rows
from backend.app.gateway.read_policy import (
    DEFAULT_MAX_EXPLAIN_ROWS,
    GatewayDecision,
    check_read_sql,
)
from backend.app.results.store import ResultStore, ResultStoreError, ResultWriteMeta
from backend.app.types import CompiledQuery, QueryTask, RuntimeContext, SkillErrorCode

_BATCH = 1000
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ExecuteReadError(Exception):
    def __init__(self, code: SkillErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def execute_read(
    query: CompiledQuery,
    ctx: RuntimeContext,
    *,
    task: QueryTask,
    catalog: CatalogSnapshot,
    store: ResultStore,
    allowed_joins: list[TableRelation] | None = None,
    engine: Engine | None = None,
    timeout_seconds: int = 30,
    max_explain_rows: int = DEFAULT_MAX_EXPLAIN_ROWS,
    max_retries: int = 1,
) -> str:
    joins = list(allowed_joins or [])
    _raise_unless_ok(
        check_read_sql(
            query,
            task,
            catalog,
            joins,
            permissions=ctx.permissions,
            max_explain_rows=max_explain_rows,
        )
    )
    if engine is None:
        from backend.app.mysql.pool import get_engine

        engine = get_engine("reader")

    explain_rows = estimate_explain_rows(query.sql, query.params, engine=engine)
    _raise_unless_ok(
        check_read_sql(
            query,
            task,
            catalog,
            joins,
            permissions=ctx.permissions,
            explain_rows=explain_rows,
            max_explain_rows=max_explain_rows,
        )
    )

    stmt = _bound_statement(query)
    timeout_ms = timeout_seconds * 1000
    metrics = {m.metric_id: m for m in catalog.metrics}
    meta = ResultWriteMeta(
        user_id=ctx.user_id,
        thread_id=ctx.thread_id,
        parent_result_id=task.parent_result_id,
        permission_version=ctx.permissions.permission_version,
        catalog_version=catalog.catalog_version,
        time_range=task.time_range,
        request_time_utc=ctx.request_time_utc,
        metric_versions={mid: metrics[mid].version for mid in task.metric_ids if mid in metrics},
    )

    last_error: BaseException | None = None
    for attempt in range(max_retries + 1):
        result_id = store.create_writing(meta)
        try:
            data_as_of = _stream_into_store(
                engine,
                stmt,
                query.params,
                store,
                result_id,
                timeout_ms,
                task,
                catalog,
                ctx,
            )
            return store.finalize(result_id, data_as_of=data_as_of).result_id
        except ExecuteReadError:
            store.abort(result_id)
            raise
        except ResultStoreError as exc:
            store.abort(result_id)
            if exc.code == SkillErrorCode.TOO_BROAD or attempt == max_retries:
                raise ExecuteReadError(exc.code, str(exc)) from exc
            last_error = exc
        except SQLAlchemyError as exc:
            store.abort(result_id)
            if attempt == max_retries:
                raise ExecuteReadError(SkillErrorCode.REJECTED, str(exc)) from exc
            last_error = exc
    raise ExecuteReadError(SkillErrorCode.REJECTED, str(last_error))


def _raise_unless_ok(decision: GatewayDecision) -> None:
    if decision.ok:
        return
    code = SkillErrorCode.TOO_BROAD if decision.kind == "too_broad" else SkillErrorCode.UNSAFE_SQL
    raise ExecuteReadError(code, decision.reason or "gateway rejected")


def _bound_statement(query: CompiledQuery):
    stmt = text(query.sql)
    expanding = [
        bindparam(name, expanding=True)
        for name, value in query.params.items()
        if isinstance(value, (list, tuple))
    ]
    if expanding:
        stmt = stmt.bindparams(*expanding)
    return stmt


def _stream_into_store(
    engine: Engine,
    stmt,
    params: dict[str, Any],
    store: ResultStore,
    result_id: str,
    timeout_ms: int,
    task: QueryTask,
    catalog: CatalogSnapshot,
    ctx: RuntimeContext,
) -> str:
    with engine.connect() as conn:
        conn.execute(text("SET SESSION max_execution_time = :ms"), {"ms": timeout_ms})
        result = conn.execution_options(stream_results=True).execute(stmt, params)
        mapped = result.mappings()
        while True:
            batch = mapped.fetchmany(_BATCH)
            if not batch:
                break
            store.append_rows(result_id, [dict(row) for row in batch])
        return _data_as_of(conn, task, catalog, ctx)


def _data_as_of(conn, task: QueryTask, catalog: CatalogSnapshot, ctx: RuntimeContext) -> str:
    start = _parse_dt(task.time_range.start)
    request = _parse_dt(ctx.request_time_utc)
    by_id = {m.metric_id: m for m in catalog.metrics}
    tables = {t.table_name for t in catalog.tables}
    columns = {(c.table_name, c.column_name) for c in catalog.columns}
    seen: set[str] = set()
    peaks: list[datetime] = []
    for metric_id in task.metric_ids:
        metric = by_id.get(metric_id)
        if metric is None:
            continue
        time_field = metric.time_field
        if time_field in seen:
            continue
        seen.add(time_field)
        table, _, column = time_field.partition(".")
        if not column or table not in tables or (table, column) not in columns:
            raise ExecuteReadError(SkillErrorCode.REJECTED, f"unknown time_field {time_field}")
        value = conn.execute(
            text(f"SELECT MAX({_quote_ident(column)}) FROM {_quote_ident(table)}"),
            {},
        ).scalar()
        peaks.append(start if value is None else _parse_dt(value))
    latest = max(peaks) if peaks else start
    return min(request, latest).isoformat()


def _quote_ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ExecuteReadError(SkillErrorCode.REJECTED, f"invalid identifier: {name}")
    return f"`{name}`"


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text_value = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text_value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
