from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class Intent(str, Enum):
    QUERY = "query"
    WRITE = "write"
    FOLLOWUP = "followup"  # 仅表示继续上一轮查询；filter vs requery 由查询 Skill 决定
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"


class TimeRange(BaseModel):
    start: str  # ISO8601, inclusive
    end: str  # ISO8601, exclusive  [start, end)
    grain: Literal["day", "week", "month"] = "day"
    label: str  # "2026-08" / "今天" 解析后的展示名
    source: Literal["user", "server_default"] = "server_default"


class PermissionSet(BaseModel):
    tenant_id: str
    user_id: str
    role: Literal["analyst", "operator"]
    allowed_tables: list[str]
    allowed_columns: list[str]  # "db.table.column"
    allowed_metrics: list[str]
    allowed_write_ops: list[str]
    catalog_version: int
    permission_version: int


class RuntimeContext(BaseModel):
    tenant_id: str
    user_id: str
    role: Literal["analyst", "operator"]
    request_time_utc: str
    timezone: str
    permissions: PermissionSet
    thread_id: str


class FilterCond(BaseModel):
    field: str
    op: Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "like"]
    value: Any


class LocalFilterSpec(BaseModel):
    filters: list[FilterCond] = []
    order_by: list[str] = []
    select: list[str] = []
    topn: int | None = None


class QueryTask(BaseModel):
    task_id: str
    metric_ids: list[str]
    dimensions: list[str]
    filters: list[FilterCond]
    time_range: TimeRange
    order_by: list[str] = []
    limit: int | None = None
    parent_result_id: str | None = None
    catalog_version: int
    permission_version: int


class WriteTask(BaseModel):
    task_id: str
    operation_type: str
    object_ids: list[str]
    params: dict[str, Any]
    filters: list[FilterCond] = []
    permission_version: int


class ResultSummary(BaseModel):
    result_id: str
    row_count: int
    columns: list[str]
    preview_rows: list[dict[str, Any]]  # API/前端 ≤20 行；禁止送进 respond Prompt
    units: dict[str, str] = {}
    time_range: TimeRange
    data_as_of: str
    metric_versions: dict[str, int] = {}
    schema_version: int  # 必须等于当时的 catalog_version
    parent_result_id: str | None = None


class CompiledQuery(BaseModel):
    sql: str  # 仅命名参数，例如 WHERE status IN :statuses
    params: dict[str, Any]


class SkillErrorCode(str, Enum):
    SCHEMA_GAP = "SCHEMA_GAP"
    AMBIGUOUS = "AMBIGUOUS"
    UNSAFE_SQL = "UNSAFE_SQL"
    TOO_BROAD = "TOO_BROAD"
    RESULT_EXPIRED = "RESULT_EXPIRED"
    PERMISSION_CHANGED = "PERMISSION_CHANGED"
    WRITE_SCOPE_TOO_LARGE = "WRITE_SCOPE_TOO_LARGE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    UNKNOWN_COMMIT = "UNKNOWN"
    UNSUPPORTED_ANALYSIS = "UNSUPPORTED_ANALYSIS"
    REJECTED = "REJECTED"


class QuerySkillResult(BaseModel):
    ok: bool
    result: ResultSummary | None = None
    error_code: SkillErrorCode | None = None
    error_message: str | None = None
    hitl: dict[str, Any] | None = None


class WriteSkillResult(BaseModel):
    ok: bool
    operation_id: str | None = None
    status: Literal["preview", "committed", "rejected", "unknown"] | None = None
    affected_rows: int | None = None
    audit_id: str | None = None
    preview: dict[str, Any] | None = None
    error_code: SkillErrorCode | None = None
    error_message: str | None = None


class QuerySkeleton(BaseModel):
    metric_ids: list[str]  # 同一任务可多指标；编译器按 grain_table 拆 CTE
    select_dims: list[str]
    from_table: str
    joins: list[dict[str, str]]  # {left, right, on_left, on_right, cardinality}
    filters: list[FilterCond]
    time_field: str
    group_by: list[str]
    comparisons: list[Literal["yoy", "mom", "ratio", "topn"]] = []
    limit: int | None = None


class WritePlan(BaseModel):
    operation_type: str
    object_ids: list[str]
    params: dict[str, Any]
    filters: list[FilterCond] = []


class SchemaGap(BaseModel):
    missing_concept: str
    purpose: str
    constraints: list[str] = []
    excluded: list[str] = []


class SchemaBundle(BaseModel):
    tables: list[str]
    columns: list[str]  # "table.column"
    joins: list[dict[str, str]]  # {left, right, on_left, on_right, cardinality}
    catalog_version: int


class Ambiguous(BaseModel):
    reason: str
    paths: list[list[str]] = []
