"""The versioned, cross-module Pydantic contracts from the project specs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Intent(StrEnum):
    """Task intent recognized by the runtime.

    Spec 00 §4.1 defines only the four canonical values:

        DATA_QUERY, SCHEMA_LOOKUP, CLARIFICATION, UNSUPPORTED

    The remaining values were introduced by later specs (spec 03
    RuntimeGraph, spec 05 Memory and Interrupts) and are kept here so
    downstream code that already references them does not need to be
    rewritten as part of spec 00. They are *not* part of the spec 00
    contract surface and may be revisited when those specs land their
    own cleanups.
    """

    DATA_QUERY = "DATA_QUERY"
    SCHEMA_LOOKUP = "SCHEMA_LOOKUP"  # legacy wire value
    SCHEMA_QUERY = "SCHEMA_QUERY"  # spec 03+
    DATA_MUTATION = "DATA_MUTATION"  # spec 06
    RESULT_TRANSFORM = "RESULT_TRANSFORM"  # spec 05+
    METRIC_EXPLANATION = "METRIC_EXPLANATION"  # spec 03+
    CHAT_OR_OUT_OF_SCOPE = "CHAT_OR_OUT_OF_SCOPE"  # spec 03+
    CLARIFICATION = "CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"


class Action(StrEnum):
    RETRIEVE = "RETRIEVE"
    GENERATE = "GENERATE"
    EXECUTE = "EXECUTE"
    ASK_USER = "ASK_USER"
    RESPOND = "RESPOND"
    END = "END"
    FAIL = "FAIL"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"


class ResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class CoverageStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class ScopeMode(StrEnum):
    ALL = "ALL"
    ALLOWLIST = "ALLOWLIST"
    NONE = "NONE"


class ArtifactType(StrEnum):
    FIELD_LIST = "FIELD_LIST"
    RESULT_TABLE = "RESULT_TABLE"
    CSV = "CSV"
    CHART_DSL = "CHART_DSL"


class TimeRange(Contract):
    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"

    @field_validator("end")
    @classmethod
    def end_after_start(cls, value: datetime, info: Any) -> datetime:
        start = info.data.get("start")
        if start and value <= start:
            raise ValueError("time_range must be a non-empty half-open interval")
        return value


class FilterSpec(Contract):
    field: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "IN", "LIKE"]
    value: Any
    source: Literal["catalog", "user", "permission"] = "user"


class TaskFrame(Contract):
    task_id: str
    user_id: str
    question: str
    intent: Intent
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_range: TimeRange | None = None
    timezone: str = "Asia/Shanghai"
    explicit_conditions: list[str] = Field(default_factory=list)
    deliverables: list[Literal["DATA_TABLE", "CSV", "CHART", "TEXT"]] = Field(
        default_factory=lambda: ["TEXT"]
    )
    mentions: dict[str, list[str]] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    schema_version: str = "task_frame_v1"


class PermissionContext(Contract):
    user_id: str
    roles: list[str] = Field(default_factory=list)
    scope_mode: ScopeMode = ScopeMode.NONE
    allowed_shop_ids: list[str] = Field(default_factory=list)
    denied_classifications: list[str] = Field(default_factory=lambda: ["PHONE", "ID_CARD"])
    policy_version: str
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_source_ids: list[str] = Field(default_factory=list)
    object_scope_ref: str | None = None
    row_scope_refs: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None
    schema_version: str = "permission_context_v1"

    @model_validator(mode="after")
    def empty_allowlist_becomes_none(self) -> PermissionContext:
        # Spec 00 §4.1: ALLOWLIST with no shops is treated as NONE —
        # the default-deny posture. Coercion happens here so callers can
        # keep expressing "ALLOWLIST" intent while the runtime sees NONE.
        if self.scope_mode == ScopeMode.ALLOWLIST and not self.allowed_shop_ids:
            self.scope_mode = ScopeMode.NONE
        return self


class ContextFrame(Contract):
    context_id: str
    catalog_version: str
    permission_policy_version: str
    object_ids: list[str] = Field(default_factory=list)
    field_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    join_paths: list[str] = Field(default_factory=list)
    created_at: datetime
    schema_version: str = "context_frame_v1"


class SchemaGap(Contract):
    gap_id: str
    missing_concepts: list[str]
    candidate_object_ids: list[str] = Field(default_factory=list)
    narrow_query: str
    reason: str
    retrieval_round: int = Field(ge=1, le=2)
    schema_version: str = "schema_gap_v1"


class CoverageResult(Contract):
    status: CoverageStatus
    covered: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    ambiguous: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    schema_gap: SchemaGap | None = None
    schema_version: str = "coverage_v1"


class CatalogObject(Contract):
    object_id: str
    name: str
    grain: str
    source_id: str
    domain: str
    score: float = Field(ge=0, le=1)
    retrieval_method: str = "memory"
    index_version: str = "catalog_index_v1"
    permission_policy_version: str


class CatalogField(Contract):
    field_id: str
    name: str
    data_type: str
    nullable: bool = True
    classification: str = "BUSINESS"
    aliases: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=1)
    object_id: str
    retrieval_method: str = "memory"
    index_version: str = "catalog_index_v1"
    permission_policy_version: str


class JoinPath(Contract):
    join_id: str
    left: str
    right: str
    cardinality: str
    verified: bool = True
    hops: int = Field(default=1, ge=1, le=2)


class GroundedContext(Contract):
    context_id: str
    catalog_version: str
    objects: list[CatalogObject] = Field(default_factory=list)
    fields: list[CatalogField] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    join_paths: list[JoinPath] = Field(default_factory=list)
    coverage: CoverageStatus
    token_count: int = Field(ge=0)
    tokenizer_version: str = "cl100k_base_estimate_v1"
    permission_policy_version: str
    model_traces: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: str = "grounded_context_v1"


class QuerySpec(Contract):
    query_id: str
    metric_refs: list[str] = Field(default_factory=list)
    dimension_refs: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    time_range: TimeRange | None = None
    time_field: str | None = None
    join_path_refs: list[str] = Field(default_factory=list)
    allowed_object_ids: list[str] = Field(default_factory=list)
    expected_columns: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    schema_version: str = "query_spec_v1"


class QueryPlan(Contract):
    query_plan_id: str
    query_spec: QuerySpec
    candidate_sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    catalog_version: str
    permission_policy_version: str
    generator: str = "deterministic"
    schema_version: str = "query_plan_v1"


class ResultSummary(Contract):
    row_count: int = Field(ge=0)
    columns: list[str] = Field(default_factory=list)
    empty: bool = False
    preview: list[dict[str, Any]] = Field(default_factory=list)


class TraceFields(Contract):
    original_sql_hash: str | None = None
    rewritten_sql_hash: str | None = None
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rls_injected: bool = False
    explain_cost: float | None = None
    row_count: int | None = None
    duration_ms: float | None = None
    error_code: str | None = None


class ResultObservation(Contract):
    status: ResultStatus
    result_id: str | None = None
    summary: ResultSummary | None = None
    error_code: str | None = None
    query_plan_id: str
    catalog_version: str
    permission_policy_version: str
    trace: TraceFields = Field(default_factory=TraceFields)
    schema_version: str = "result_observation_v1"


class ArtifactSpec(Contract):
    artifact_id: str
    conversation_id: str
    owner_user_id: str
    type: ArtifactType
    source_result_ids: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    permission_policy_version: str
    catalog_version: str
    created_at: datetime
    expires_at: datetime
    payload_ref: str
    schema_version: str = "artifact_v1"


class Interrupt(Contract):
    status: Literal["WAITING_FOR_USER"] = "WAITING_FOR_USER"
    reason: str
    question: str
    candidates: list[str] = Field(default_factory=list)
    resume_node: str = "agent_node"
    checkpoint_id: str
    interrupt_id: str
    expires_at: datetime
    preview: MutationPreview | None = None
    schema_version: str = "interrupt_v1"


class SummaryFact(Contract):
    text: str
    source: Literal["USER_CONFIRMED", "SYSTEM_OBSERVED", "MODEL_INFERRED"]


class RollingSummary(Contract):
    facts: list[SummaryFact] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    pending_mutation: dict[str, Any] | None = None
    schema_version: str = "rolling_summary_v1"


class AgentState(Contract):
    thread_id: str
    request_id: str
    user_id: str
    status: RunStatus = RunStatus.RUNNING
    task_frame: TaskFrame | None = None
    previous_task_frame: TaskFrame | None = None
    context_frame: ContextFrame | None = None
    grounded_context_id: str | None = None
    grounded_context: GroundedContext | None = None
    catalog_version: str | None = None
    coverage: CoverageStatus | Literal["UNKNOWN"] = "UNKNOWN"
    schema_gap: SchemaGap | None = None
    query_plan_id: str | None = None
    query_plan: QueryPlan | None = None
    result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    latest_observation: ResultObservation | None = None
    previous_query_error: str | None = None
    next_action: Action = Action.RETRIEVE
    goal_checklist: dict[str, bool] = Field(default_factory=dict)
    budgets: dict[str, int | float] = Field(default_factory=dict)
    action_history: list[dict[str, Any]] = Field(default_factory=list)
    last_action_fingerprint: str | None = None
    pending_interrupt: Interrupt | None = None
    pending_mutation: MutationSpec | None = None
    pending_preview: MutationPreview | None = None
    latest_mutation: MutationObservation | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    rolling_summary: RollingSummary | None = None
    trace_id: str | None = None
    model_traces: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: str = "agent_state_v1"


class TraceContext(Contract):
    trace_id: str = Field(default_factory=lambda: f"trace_{uuid4().hex}")
    request_id: str
    thread_id: str
    user_id: str
    route: str
    started_at: datetime


class AppError(Contract):
    error_code: str
    message: str
    trace_id: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "app_error_v1"


class PasswordLoginRequest(Contract):
    account: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024, json_schema_extra={"format": "password"})


class RegisterRequest(Contract):
    account: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=10, max_length=128, json_schema_extra={"format": "password"})
    confirm_password: str = Field(
        min_length=10, max_length=128, json_schema_extra={"format": "password"}
    )
    role: Literal["USER", "ADMIN"]
    invite_code: str = Field(min_length=4, max_length=64)

    @model_validator(mode="after")
    def passwords_match(self) -> RegisterRequest:
        if self.password != self.confirm_password:
            raise ValueError("passwords do not match")
        return self


class TokenResponse(Contract):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class RegistrationResponse(Contract):
    status: Literal["registered"] = "registered"
    account: str
    role: Literal["USER", "ADMIN"]
    schema_version: Literal["registration_response_v1"] = "registration_response_v1"


class RecommendedQuestionsResponse(Contract):
    items: list[str] = Field(default_factory=list)
    schema_version: Literal["recommended_questions_v1"] = "recommended_questions_v1"


class ChatRequest(Contract):
    thread_id: str | None = None
    message: str = Field(min_length=1)
    # Identity is derived from the Bearer token. This nullable legacy field is
    # deliberately rejected by the API when it disagrees with the token.
    user_id: str | None = None
    timezone: str | None = None
    request_id: str | None = None
    expected_state_version: int | None = Field(default=None, ge=0)


class EvaluationEvidence(Contract):
    intent: str | None = None
    metric_ids: list[str] = Field(default_factory=list)
    object_names: list[str] = Field(default_factory=list)
    field_names: list[str] = Field(default_factory=list)
    coverage: str | None = None
    retrieval_rounds: int = 0
    grounded_context_tokens: int | None = None
    schema_gap_recovered: bool | None = None
    schema_version: Literal["evaluation_evidence_v1"] = "evaluation_evidence_v1"


class ChatResponse(Contract):
    request_id: str
    thread_id: str
    status: RunStatus
    answer: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    interrupt: Interrupt | None = None
    trace_id: str | None = None
    state_version: int | None = None
    evidence: EvaluationEvidence | None = None


class ModelUsage(Contract):
    models: list[str] = Field(default_factory=list)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model_duration_ms: float = Field(default=0, ge=0)


class RuntimeEvent(Contract):
    event: Literal[
        "run.started",
        "node.started",
        "node.completed",
        "interrupt.created",
        "run.completed",
        "run.failed",
        "thread.title_updated",
    ]
    thread_title: str | None = None
    request_id: str
    thread_id: str
    node: str | None = None
    action: Action | None = None
    status: RunStatus
    duration_ms: float | None = None
    error_code: str | None = None
    answer: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    interrupt: Interrupt | None = None
    state_version: int | None = None
    model_usage: ModelUsage | None = None
    evidence: EvaluationEvidence | None = None
    schema_version: Literal["runtime_event_v1"] = "runtime_event_v1"


class ResumeRequest(Contract):
    user_id: str | None = None
    answer: str = Field(min_length=1)
    client_request_id: str
    expected_state_version: int = Field(ge=0)


class IdentityResponse(Contract):
    user_id: str
    roles: list[str] = Field(default_factory=list)
    policy_version: str
    expires_at: datetime | None = None


class UserPreferences(Contract):
    values: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "user_preferences_v1"


class PreferenceUpdate(Contract):
    key: Literal["timezone", "default_shop_id", "chart_preference", "number_format"]
    value: Any
    confirmed: bool = False


class ResultPage(Contract):
    result_id: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)


class ThreadSummary(Contract):
    thread_id: str
    title: str
    updated_at: datetime


class ThreadListResponse(Contract):
    items: list[ThreadSummary] = Field(default_factory=list)


class ConversationMessage(Contract):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class ThreadDetail(Contract):
    thread_id: str
    status: RunStatus
    messages: list[ConversationMessage] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    interrupt: Interrupt | None = None
    state_version: int | None = None


class ArtifactRecord(Contract):
    spec: ArtifactSpec
    payload: Any


class Checkpoint(Contract):
    checkpoint_id: str
    thread_id: str
    state_version: int = Field(ge=0)
    parent_checkpoint_id: str | None = None
    status: RunStatus
    serialized_state_ref: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class MutationSpec(Contract):
    operation: Literal["INSERT", "UPDATE"]
    table: str
    filters: dict[str, Any] = Field(default_factory=dict)
    changes: dict[str, Any] = Field(default_factory=dict)
    user_reason: str
    request_id: str
    user_id: str
    permission_policy_version: str
    data_version: str
    idempotency_key: str


class MutationPreview(Contract):
    preview_id: str
    operation: str
    target: str
    diff: dict[str, Any]
    estimated_affected_rows: int
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    expires_at: datetime
    data_version: str
    permission_policy_version: str
    mutation_spec: MutationSpec
    schema_version: str = "mutation_preview_v1"


class MutationObservation(Contract):
    status: ResultStatus
    preview_id: str
    affected_rows: int = 0
    after: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    data_version: str
    permission_policy_version: str
    audit_id: str | None = None
    schema_version: str = "mutation_observation_v1"
