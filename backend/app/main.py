from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from backend.app.api import auth, chat, interrupts, results
from backend.app.logging import setup_logging
from backend.app.results.store import ResultStore

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
) -> FastAPI:
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
        if invoke_fn is None:
            invoke_fn = invoke_coordinator
    timezone = timezone or "Asia/Shanghai"
    max_rows = 100000 if max_rows is None else max_rows

    application = FastAPI(title="data-agent")
    application.state.users_db = Path(users_db)
    application.state.runtime_db = Path(runtime_db)
    application.state.result_store = result_store
    application.state.graph = graph
    application.state.invoke_fn = invoke_fn
    application.state.sessions = {}
    application.state.max_rows = max_rows
    application.state.timezone = timezone
    application.state.request_time_utc = request_time_utc
    application.state.catalog_version = catalog_version
    application.include_router(auth.router)
    application.include_router(chat.router)
    application.include_router(interrupts.router)
    application.include_router(results.router)
    return application


try:
    app = create_app()
except FileNotFoundError:
    app = FastAPI(title="data-agent")
