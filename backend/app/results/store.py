from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from pydantic import BaseModel, Field

from backend.app.results.duckdb_filter import filter_parquet
from backend.app.results.parquet import LimitExceeded, ParquetStreamWriter, parse_byte_size
from backend.app.types import (
    LocalFilterSpec,
    ResultSummary,
    RuntimeContext,
    SkillErrorCode,
    TimeRange,
)

_TENANT = "default"


class ResultStoreError(Exception):
    def __init__(self, code: SkillErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class ResultWriteMeta(BaseModel):
    user_id: str
    thread_id: str | None = None
    parent_result_id: str | None = None
    permission_version: int
    catalog_version: int
    time_range: TimeRange
    request_time_utc: str
    data_as_of: str | None = None
    metric_versions: dict[str, int] = Field(default_factory=dict)


class ResultStore:
    def __init__(
        self,
        *,
        results_db: str | Path,
        results_dir: str | Path,
        ttl_hours: int = 1,
        max_rows: int = 100000,
        max_bytes: int | str = "256MB",
    ) -> None:
        self.results_db = Path(results_db)
        self.results_dir = Path(results_dir)
        self.ttl_hours = ttl_hours
        self.max_rows = max_rows
        self.max_bytes = parse_byte_size(max_bytes)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._writers: dict[str, ParquetStreamWriter] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _result_lock(self, result_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(result_id, threading.Lock())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.results_db)
        conn.row_factory = sqlite3.Row
        return conn

    def _part_path(self, result_id: str) -> Path:
        return self.results_dir / f"{result_id}.part"

    def _parquet_path(self, result_id: str) -> Path:
        return self.results_dir / f"{result_id}.parquet"

    def create_writing(self, meta: ResultWriteMeta) -> str:
        result_id = str(uuid.uuid4())
        created = _as_utc(meta.request_time_utc)
        expires = created + timedelta(hours=self.ttl_hours)
        with self._result_lock(result_id):
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO query_result (
                        result_id, thread_id, user_id, status, parquet_path,
                        row_count, columns_json, parent_result_id, time_range_json,
                        permission_version, catalog_version, schema_version,
                        data_as_of, metric_versions_json, created_at, expires_at
                    ) VALUES (?, ?, ?, 'WRITING', ?, NULL, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result_id,
                        meta.thread_id,
                        meta.user_id,
                        str(self._parquet_path(result_id)),
                        meta.parent_result_id,
                        meta.time_range.model_dump_json(),
                        meta.permission_version,
                        meta.catalog_version,
                        meta.catalog_version,
                        meta.data_as_of,
                        json.dumps(meta.metric_versions),
                        created.isoformat(),
                        expires.isoformat(),
                    ),
                )
                conn.commit()
            part = self._part_path(result_id)
            part.touch()
            self._writers[result_id] = ParquetStreamWriter(
                part, max_rows=self.max_rows, max_bytes=self.max_bytes
            )
        return result_id

    def append_rows(self, result_id: str, rows: list[dict[str, Any]]) -> None:
        with self._result_lock(result_id):
            writer = self._writers.get(result_id)
            if writer is None:
                raise ResultStoreError(SkillErrorCode.REJECTED, f"not writing: {result_id}")
            try:
                writer.write_batch(rows)
            except LimitExceeded as exc:
                self._abort_locked(result_id)
                raise ResultStoreError(SkillErrorCode.TOO_BROAD, str(exc)) from exc

    def finalize(self, result_id: str, *, data_as_of: str) -> ResultSummary:
        with self._result_lock(result_id):
            writer = self._writers.pop(result_id, None)
            if writer is None:
                raise ResultStoreError(SkillErrorCode.REJECTED, f"not writing: {result_id}")
            if writer.row_count == 0:
                writer.write_empty(writer.columns)
            writer.close()
            part = self._part_path(result_id)
            parquet = self._parquet_path(result_id)
            part.replace(parquet)
            columns = writer.columns
            row_count = writer.row_count
            with self._connect() as conn:
                conn.execute(
                    """UPDATE query_result
                       SET status = 'READY', row_count = ?, columns_json = ?, data_as_of = ?
                       WHERE result_id = ?""",
                    (row_count, json.dumps(columns), data_as_of, result_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM query_result WHERE result_id = ?", (result_id,)
                ).fetchone()
            return _summary(row, preview=_read_page(parquet, columns, 0, 20), data_as_of=data_as_of)

    def abort(self, result_id: str) -> None:
        with self._result_lock(result_id):
            self._abort_locked(result_id)

    def _abort_locked(self, result_id: str) -> None:
        writer = self._writers.pop(result_id, None)
        if writer is not None:
            writer.close()
        self._part_path(result_id).unlink(missing_ok=True)
        self._parquet_path(result_id).unlink(missing_ok=True)
        with self._connect() as conn:
            conn.execute(
                "UPDATE query_result SET status = 'DELETED' WHERE result_id = ?",
                (result_id,),
            )
            conn.commit()

    def read_page(
        self,
        result_id: str,
        ctx: RuntimeContext,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> ResultSummary:
        with self._result_lock(result_id):
            row = self._load_row(result_id)
            self._authorize(row, ctx)
            columns = json.loads(row["columns_json"])
            preview = _read_page(Path(row["parquet_path"]), columns, offset, limit)
            return _summary(row, preview=preview, data_as_of=row["data_as_of"] or "")

    def filter_local(
        self,
        result_id: str,
        spec: LocalFilterSpec,
        ctx: RuntimeContext,
    ) -> str:
        with self._result_lock(result_id):
            row = self._load_row(result_id)
            self._authorize(row, ctx)
            columns = json.loads(row["columns_json"])
            try:
                filtered = filter_parquet(row["parquet_path"], spec, columns)
            except ValueError as exc:
                raise ResultStoreError(SkillErrorCode.REJECTED, str(exc)) from exc
            child_meta = ResultWriteMeta(
                user_id=row["user_id"],
                thread_id=row["thread_id"],
                parent_result_id=result_id,
                permission_version=int(row["permission_version"]),
                catalog_version=int(row["catalog_version"]),
                time_range=TimeRange.model_validate_json(row["time_range_json"]),
                request_time_utc=ctx.request_time_utc,
                data_as_of=row["data_as_of"],
                metric_versions=json.loads(row["metric_versions_json"]),
            )
            data_as_of = row["data_as_of"] or ctx.request_time_utc
            out_columns = spec.select or columns
        child_id = self.create_writing(child_meta)
        try:
            if filtered:
                self.append_rows(child_id, filtered)
            else:
                with self._result_lock(child_id):
                    writer = self._writers[child_id]
                    writer.write_empty(out_columns)
            return self.finalize(child_id, data_as_of=data_as_of).result_id
        except Exception:
            self.abort(child_id)
            raise

    def sweep_ttl(self, *, now: str | None = None) -> None:
        now_dt = _as_utc(now) if now is not None else datetime.now(UTC)
        now_iso = now_dt.isoformat()
        with self._connect() as conn:
            ready_ids = [
                r["result_id"]
                for r in conn.execute(
                    "SELECT result_id FROM query_result WHERE status = 'READY' AND expires_at <= ?",
                    (now_iso,),
                )
            ]
            expired_ids = [
                r["result_id"]
                for r in conn.execute("SELECT result_id FROM query_result WHERE status = 'EXPIRED'")
            ]
            writing_ids = {
                r["result_id"]
                for r in conn.execute("SELECT result_id FROM query_result WHERE status = 'WRITING'")
            }
        for rid in ready_ids:
            with self._result_lock(rid):
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT status, expires_at FROM query_result WHERE result_id = ?",
                        (rid,),
                    ).fetchone()
                    if row is None or row["status"] != "READY":
                        continue
                    if _as_utc(row["expires_at"]) > now_dt:
                        continue
                    self._parquet_path(rid).unlink(missing_ok=True)
                    self._part_path(rid).unlink(missing_ok=True)
                    conn.execute(
                        "UPDATE query_result SET status = 'EXPIRED' WHERE result_id = ?",
                        (rid,),
                    )
                    conn.commit()
                expired_ids.append(rid)
        for rid in expired_ids:
            with self._result_lock(rid), self._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM query_result WHERE result_id = ?", (rid,)
                ).fetchone()
                if row is None or row["status"] != "EXPIRED":
                    continue
                self._parquet_path(rid).unlink(missing_ok=True)
                conn.execute(
                    "UPDATE query_result SET status = 'DELETED' WHERE result_id = ?",
                    (rid,),
                )
                conn.commit()
        for part in self.results_dir.glob("*.part"):
            rid = part.stem
            if rid in writing_ids:
                continue
            with self._result_lock(rid):
                if rid in self._writers:
                    continue
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT status FROM query_result WHERE result_id = ?", (rid,)
                    ).fetchone()
                if row is None or row["status"] != "WRITING":
                    part.unlink(missing_ok=True)

    def _load_row(self, result_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM query_result WHERE result_id = ?", (result_id,)
            ).fetchone()
        if row is None:
            raise ResultStoreError(SkillErrorCode.REJECTED, f"unknown result: {result_id}")
        return row

    def _authorize(self, row: sqlite3.Row, ctx: RuntimeContext) -> None:
        if ctx.tenant_id != _TENANT:
            raise ResultStoreError(SkillErrorCode.REJECTED, "tenant_id must be default")
        if ctx.user_id != row["user_id"]:
            raise ResultStoreError(SkillErrorCode.REJECTED, "not owner")
        if ctx.permissions.permission_version != int(row["permission_version"]):
            raise ResultStoreError(SkillErrorCode.PERMISSION_CHANGED, "permission_version changed")
        status = row["status"]
        expired = status in {"EXPIRED", "DELETED"} or _as_utc(ctx.request_time_utc) >= _as_utc(
            row["expires_at"]
        )
        if expired:
            raise ResultStoreError(SkillErrorCode.RESULT_EXPIRED, "result expired")
        if status != "READY":
            raise ResultStoreError(SkillErrorCode.REJECTED, f"result not ready: {status}")


def _as_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _read_page(path: Path, columns: list[str], offset: int, limit: int) -> list[dict[str, Any]]:
    table = pq.read_table(path, columns=columns or None)
    return table.slice(offset, limit).to_pylist()


def _summary(row: sqlite3.Row, *, preview: list[dict[str, Any]], data_as_of: str) -> ResultSummary:
    return ResultSummary(
        result_id=row["result_id"],
        row_count=int(row["row_count"] or 0),
        columns=json.loads(row["columns_json"]),
        preview_rows=preview,
        time_range=TimeRange.model_validate_json(row["time_range_json"]),
        data_as_of=data_as_of,
        metric_versions=json.loads(row["metric_versions_json"]),
        schema_version=int(row["schema_version"]),
        parent_result_id=row["parent_result_id"],
    )
