from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from backend.app.api import auth, chat, interrupts, results
from backend.app.logging import setup_logging
from backend.app.results.store import ResultStore


def _production_graph(settings, store, runtime_db: Path):
    from backend.app.catalog.store import CatalogStore
    from backend.app.coordinator.graph import build_coordinator_graph, sqlite_checkpointer
    from backend.app.llm.client import ChatLlm
    from backend.app.skills.query.graph import run_query_skill
    from backend.app.skills.write.graph import execute_write, prepare_write

    catalog_store = CatalogStore(settings.sqlite.catalog)
    try:
        from backend.app.catalog.sync import ensure_physical_schema

        ensure_physical_schema(catalog_db=catalog_store.path)
    except Exception:
        logging.getLogger(__name__).warning(
            "catalog physical schema sync failed; queries may SCHEMA_GAP",
            exc_info=True,
        )
    catalog = catalog_store.load()
    if not catalog.columns:
        logging.getLogger(__name__).warning(
            "catalog has 0 columns; queries will SCHEMA_GAP until sync_from_mysql"
        )
    llm = ChatLlm(settings.llm)

    def run_query(task, ctx, parent_task=None):
        return run_query_skill(
            task,
            ctx,
            catalog=catalog_store.load(),
            store=store,
            llm=llm,
            parent_task=parent_task,
            users_db=settings.sqlite.users,
        )

    def prepare(task, ctx):
        return prepare_write(
            task,
            ctx,
            llm=llm,
            users_db=settings.sqlite.users,
            approval_ttl_minutes=settings.write.approval_ttl_minutes,
        )

    def execute(operation_id, request_hash, ctx, preview=None):
        return execute_write(
            operation_id,
            request_hash,
            ctx,
            preview=preview or {},
            users_db=settings.sqlite.users,
            approval_ttl_minutes=settings.write.approval_ttl_minutes,
        )

    graph = build_coordinator_graph(
        llm=llm,
        catalog=catalog,
        store=store,
        run_query_fn=run_query,
        prepare_write_fn=prepare,
        execute_write_fn=execute,
        runtime_db=runtime_db,
        checkpointer=sqlite_checkpointer(settings.sqlite.checkpoint),
    )
    return graph, catalog.catalog_version

setup_logging()


def create_app(
    *,
    users_db: str | Path | None = None,
    runtime_db: str | Path | None = None,
    result_store: ResultStore | None = None,
    graph: Any | None = None,
    invoke_fn: Callable[..., Any] | None = None,
    max_rows: int | None = None,
    timezone: str | None = None,
    request_time_utc: str | None = None,
    catalog_version: int = 1,
    jwt_secret: str | None = None,
    jwt_ttl_hours: int | None = None,
    title_fn: Callable[[str], str] | None = None,
) -> FastAPI:
    settings = None
    if users_db is None or runtime_db is None or result_store is None or max_rows is None:
        from backend.app.config import load_settings
        from backend.app.coordinator.graph import invoke_coordinator

        settings = load_settings()
        users_db = users_db or settings.sqlite.users
        runtime_db = runtime_db or settings.sqlite.runtime
        timezone = timezone or settings.app.timezone
        max_rows = max_rows if max_rows is not None else settings.results.max_rows
        if result_store is None:
            result_store = ResultStore(
                results_db=settings.sqlite.results,
                results_dir=settings.results.dir,
                ttl_hours=settings.results.ttl_hours,
                max_rows=settings.results.max_rows,
                max_bytes=settings.results.max_bytes,
            )
        if title_fn is None:
            title_fn = chat.default_title_fn
        if graph is None:
            graph, catalog_version = _production_graph(settings, result_store, Path(runtime_db))
        if invoke_fn is None:
            invoke_fn = invoke_coordinator
    timezone = timezone or "Asia/Shanghai"
    max_rows = 100000 if max_rows is None else max_rows
    jwt_secret = jwt_secret or "change-me-change-me-change-me-32b"
    jwt_ttl_hours = 24 if jwt_ttl_hours is None else jwt_ttl_hours
    title_fn = title_fn or chat.default_title_fn

    application = FastAPI(title="data-agent")
    application.state.users_db = Path(users_db)
    application.state.runtime_db = Path(runtime_db)
    application.state.result_store = result_store
    application.state.graph = graph
    application.state.invoke_fn = invoke_fn
    application.state.max_rows = max_rows
    application.state.timezone = timezone
    application.state.request_time_utc = request_time_utc
    application.state.catalog_version = catalog_version
    application.state.jwt_secret = jwt_secret
    application.state.jwt_ttl_hours = jwt_ttl_hours
    application.state.title_fn = title_fn
    application.include_router(auth.router)
    application.include_router(chat.router)
    application.include_router(interrupts.router)
    application.include_router(results.router)
    return application


try:
    app = create_app()
except FileNotFoundError:
    app = FastAPI(title="data-agent")
