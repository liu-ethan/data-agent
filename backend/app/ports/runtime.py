"""Stable interfaces between graph logic and infrastructure adapters.

The runtime depends on these protocols; MySQL, Milvus and local test doubles
implement them.  This keeps a test adapter from becoming an implicit
production fallback.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..models import (
    CoverageResult,
    GroundedContext,
    MutationObservation,
    MutationPreview,
    MutationSpec,
    PermissionContext,
    QueryPlan,
    ResultObservation,
    SchemaGap,
    TaskFrame,
)


class DataQueryPort(Protocol):
    def healthcheck(self) -> bool: ...

    def explain(self, sql: str, parameters: dict[str, Any]) -> tuple[float, int]: ...

    def fetch(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]: ...


class DataMutationPort(Protocol):
    def writer_identity(self) -> str: ...

    def fetch_target(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]: ...

    def execute_write(self, sql: str, parameters: dict[str, Any]) -> int: ...


class ResultRepositoryPort(Protocol):
    def save(self, rows: list[dict[str, Any]], *, owner_user_id: str | None = None) -> str: ...


class ReadGatewayPort(Protocol):
    def execute(self, plan: QueryPlan, permission: PermissionContext) -> ResultObservation: ...


class WriteGatewayPort(Protocol):
    def preview(self, spec: MutationSpec, permission: PermissionContext) -> MutationPreview: ...

    def commit(self, preview: MutationPreview, permission: PermissionContext) -> MutationObservation: ...


class CatalogRetrievalPort(Protocol):
    def retrieve(
        self,
        task: TaskFrame,
        permission: PermissionContext,
        schema_gap: SchemaGap | None = None,
        existing_context_id: str | None = None,
        existing_context: GroundedContext | None = None,
    ) -> tuple[GroundedContext, CoverageResult] | Any: ...


class StructuredLLMPort(Protocol):
    async def structured(self, **kwargs: Any) -> tuple[Any, Any]: ...


class RuntimeStateStorePort(Protocol):
    def append_event(self, request_id: str, user_id: str, event: dict[str, Any]) -> int: ...

    def save_checkpoint(self, state: Any, **kwargs: Any) -> Any: ...

    def checkpoint(self, thread_id: str) -> Any: ...

    def load_state(self, thread_id: str) -> Any: ...

    def append_message(self, thread_id: str, user_id: str, role: str, content: str) -> Any: ...

    def create_artifact(self, **kwargs: Any) -> Any: ...
