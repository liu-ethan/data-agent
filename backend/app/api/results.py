from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from backend.app.api.auth import CurrentUser
from backend.app.api.chat import build_ctx
from backend.app.results.store import ResultStoreError
from backend.app.types import SkillErrorCode

router = APIRouter()


def _raise_store_error(exc: ResultStoreError) -> None:
    if exc.code == SkillErrorCode.RESULT_EXPIRED:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if exc.code == SkillErrorCode.PERMISSION_CHANGED:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    message = str(exc)
    if "not owner" in message:
        raise HTTPException(status_code=403, detail=message) from exc
    if "unknown result" in message:
        raise HTTPException(status_code=404, detail=message) from exc
    raise HTTPException(status_code=409, detail=message) from exc


def _read_page(request: Request, result_id: str, user: CurrentUser, *, offset: int, limit: int):
    ctx = build_ctx(request, user.user_id, thread_id="results")
    store = request.app.state.result_store
    try:
        return store.read_page(result_id, ctx, offset=offset, limit=limit)
    except ResultStoreError as exc:
        _raise_store_error(exc)


@router.get("/api/results/{result_id}.csv")
def export_csv(result_id: str, request: Request, user: CurrentUser):
    max_rows = int(request.app.state.max_rows)
    summary = _read_page(request, result_id, user, offset=0, limit=max_rows)
    cap = min(summary.row_count, max_rows)
    if len(summary.preview_rows) > cap:
        rows = summary.preview_rows[:cap]
    else:
        rows = summary.preview_rows
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=summary.columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{result_id}.csv"'},
    )


@router.get("/api/results/{result_id}")
def get_result(result_id: str, request: Request, user: CurrentUser, offset: int = 0, limit: int = 20):
    max_rows = int(request.app.state.max_rows)
    limit = min(max(limit, 1), max_rows)
    offset = max(offset, 0)
    summary = _read_page(request, result_id, user, offset=offset, limit=limit)
    return {
        "result_id": summary.result_id,
        "row_count": summary.row_count,
        "columns": summary.columns,
        "rows": summary.preview_rows,
        "offset": offset,
        "limit": limit,
        "time_range": summary.time_range.model_dump(),
        "data_as_of": summary.data_as_of,
        "metric_versions": summary.metric_versions,
        "schema_version": summary.schema_version,
        "parent_result_id": summary.parent_result_id,
    }
