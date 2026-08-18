"""WriteGateway: the only path from a MutationSpec to a database write."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..errors import RuntimeAgentError
from ..models import (
    MutationObservation,
    MutationPreview,
    MutationSpec,
    PermissionContext,
    ResultStatus,
)
from ..ports import DataMutationPort
from ..services.trace import record

WRITE_ALLOWLIST = {("UPDATE", "products"): frozenset({"product_name"})}
UNIQUE_KEYS = {"products": ("product_id",)}
FIELD_TYPES = {"products.product_id": str, "products.product_name": str}
WRITER_ACCOUNT = "agent_writer"
MIGRATION_ACCOUNT = "agent_migration"


class WriteGateway:
    def __init__(
        self,
        *,
        data: DataMutationPort,
        auditor: Any | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.data = data
        self.auditor = auditor
        settings = settings or {}
        self.max_affected_rows = int(settings.get("max_affected_rows", 1))
        self.preview_ttl_seconds = int(settings.get("preview_ttl_seconds", 900))
        self._commits: dict[str, MutationObservation] = {}

    def preview(self, spec: MutationSpec, permission: PermissionContext) -> MutationPreview:
        self._require_admin(permission)
        self._require_actor(spec, permission)
        self._ensure_audit_ready()
        self._validate_spec(spec)
        sql, params = self._select_sql(spec)
        rows = self.data.fetch_target(sql, params)
        if not rows:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "mutation target does not exist")
        if len(rows) > self.max_affected_rows:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "estimated affected rows exceed the write limit")
        before = dict(rows[0])
        diff = {
            field: {"before": before.get(field), "after": value}
            for field, value in spec.changes.items()
        }
        data_version = _row_version(spec.table, before)
        snapshot = spec.model_copy(update={
            "permission_policy_version": permission.policy_version,
            "data_version": data_version,
        })
        preview = MutationPreview(
            preview_id=f"preview_{uuid4().hex[:16]}",
            operation=spec.operation,
            target=_target(spec),
            diff=diff,
            estimated_affected_rows=len(rows),
            risk_level="MEDIUM",
            expires_at=datetime.now(UTC) + timedelta(seconds=self.preview_ttl_seconds),
            data_version=data_version,
            permission_policy_version=permission.policy_version,
            mutation_spec=snapshot,
        )
        record(
            "write_gateway.previewed",
            preview_id=preview.preview_id,
            table=spec.table,
            user_id=permission.user_id,
        )
        return preview

    def commit(
        self, preview: MutationPreview, permission: PermissionContext
    ) -> MutationObservation:
        self._require_admin(permission)
        spec = preview.mutation_spec
        self._require_actor(spec, permission)
        if (
            permission.policy_version != preview.permission_policy_version
            or permission.policy_version != spec.permission_policy_version
        ):
            raise RuntimeAgentError("MUTATION_STALE", "permission policy changed after approval")
        if preview.expires_at <= datetime.now(UTC):
            raise RuntimeAgentError("MUTATION_STALE", "mutation preview has expired")
        existing = self._committed(spec.idempotency_key)
        if existing is not None:
            return existing.model_copy(update={"affected_rows": 0})
        self._validate_spec(spec)
        sql, params = self._select_sql(spec)
        rows = self.data.fetch_target(sql, params)
        if not rows:
            raise RuntimeAgentError("MUTATION_STALE", "mutation target no longer exists")
        current = dict(rows[0])
        current_version = _row_version(spec.table, current)
        if current_version != preview.data_version or current_version != spec.data_version:
            raise RuntimeAgentError("MUTATION_STALE", "target data changed after approval")
        self._ensure_audit_ready()
        write_sql, write_params = self._write_sql(spec)
        try:
            affected = self.data.execute_write(write_sql, write_params)
        except RuntimeAgentError:
            raise
        except Exception as exc:
            raise RuntimeAgentError(
                "MUTATION_EXECUTION_FAILED", "The database could not execute the write"
            ) from exc
        if affected > self.max_affected_rows:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "affected rows exceed the write limit")
        after = dict(current)
        after.update(spec.changes)
        after_version = _row_version(spec.table, after)
        audit_id = self._record_audit(
            spec=spec,
            preview=preview,
            permission=permission,
            before=current,
            after=after,
            affected_rows=affected,
        )
        observation = MutationObservation(
            status=ResultStatus.SUCCESS,
            preview_id=preview.preview_id,
            affected_rows=affected,
            after={field: after[field] for field in spec.changes},
            data_version=after_version,
            permission_policy_version=permission.policy_version,
            audit_id=audit_id,
        )
        self._store_commit(spec.idempotency_key, observation)
        record(
            "write_gateway.committed",
            preview_id=preview.preview_id,
            audit_id=audit_id,
            affected_rows=affected,
        )
        return observation

    def _require_admin(self, permission: PermissionContext) -> None:
        roles = {role.upper() for role in permission.roles}
        if "ADMIN" not in roles:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "only an admin may mutate data")

    def _require_actor(self, spec: MutationSpec, permission: PermissionContext) -> None:
        if spec.user_id != permission.user_id:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "mutation actor does not match the caller")
        identity = self.data.writer_identity()
        username = identity.split("@", 1)[0]
        if username == MIGRATION_ACCOUNT:
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "migration account cannot be used on the write path",
            )
        if username != WRITER_ACCOUNT:
            raise RuntimeAgentError(
                "WRITER_ACCOUNT_INVALID",
                "write path did not authenticate as the configured writer",
            )

    def _ensure_audit_ready(self) -> None:
        if self.auditor is None:
            return
        ensure = getattr(self.auditor, "ensure_mutation_audit", None)
        if ensure is not None:
            ensure()

    def _validate_spec(self, spec: MutationSpec) -> None:
        allowed_fields = WRITE_ALLOWLIST.get((spec.operation, spec.table))
        if not allowed_fields:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "table or operation is not on the write allowlist")
        if not spec.changes or set(spec.changes) - allowed_fields:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "changes include fields outside the write allowlist")
        unique_key = UNIQUE_KEYS.get(spec.table)
        if spec.operation == "UPDATE":
            if unique_key is None or tuple(spec.filters) != unique_key:
                raise RuntimeAgentError(
                    "WRITE_FORBIDDEN",
                    "UPDATE requires exactly one registered unique-key equality filter",
                )
            if spec.filters[unique_key[0]] in (None, ""):
                raise RuntimeAgentError("WRITE_FORBIDDEN", "unique-key filter is empty")
        elif spec.filters:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "INSERT cannot include update filters")
        for field, value in {**spec.filters, **spec.changes}.items():
            expected = FIELD_TYPES.get(f"{spec.table}.{field}")
            if expected is str and not isinstance(value, str):
                raise RuntimeAgentError("WRITE_FORBIDDEN", "mutation value type does not match the catalog")
            if field in spec.changes and isinstance(value, str) and not value.strip():
                raise RuntimeAgentError("WRITE_FORBIDDEN", "mutation value type does not match the catalog")
            if isinstance(value, str) and any(token in value for token in (";", "--", "/*", "*/")):
                raise RuntimeAgentError("WRITE_FORBIDDEN", "mutation values cannot contain SQL")

    def _select_sql(self, spec: MutationSpec) -> tuple[str, dict[str, Any]]:
        key = next(iter(spec.filters))
        return (
            f"SELECT * FROM {spec.table} WHERE {key} = :f_{key}",
            {f"f_{key}": spec.filters[key]},
        )

    def _write_sql(self, spec: MutationSpec) -> tuple[str, dict[str, Any]]:
        assignments = ", ".join(f"{field} = :c_{field}" for field in spec.changes)
        params = {f"c_{field}": value for field, value in spec.changes.items()}
        if spec.operation == "INSERT":
            columns = ", ".join(spec.changes)
            placeholders = ", ".join(f":c_{field}" for field in spec.changes)
            return f"INSERT INTO {spec.table} ({columns}) VALUES ({placeholders})", params
        key = next(iter(spec.filters))
        params[f"f_{key}"] = spec.filters[key]
        return f"UPDATE {spec.table} SET {assignments} WHERE {key} = :f_{key}", params

    def _committed(self, idempotency_key: str) -> MutationObservation | None:
        if self.auditor is not None and hasattr(self.auditor, "get_idempotent"):
            payload = self.auditor.get_idempotent(_commit_key(idempotency_key))
            if payload:
                return MutationObservation.model_validate(payload)
        return self._commits.get(idempotency_key)

    def _store_commit(self, idempotency_key: str, observation: MutationObservation) -> None:
        payload = observation.model_dump(mode="json")
        if self.auditor is not None and hasattr(self.auditor, "put_idempotent"):
            stored = self.auditor.put_idempotent(_commit_key(idempotency_key), payload)
            self._commits[idempotency_key] = MutationObservation.model_validate(stored)
            return
        self._commits[idempotency_key] = observation

    def _record_audit(
        self,
        *,
        spec: MutationSpec,
        preview: MutationPreview,
        permission: PermissionContext,
        before: dict[str, Any],
        after: dict[str, Any],
        affected_rows: int,
    ) -> str:
        if self.auditor is not None and hasattr(self.auditor, "record_mutation_audit"):
            try:
                return self.auditor.record_mutation_audit(
                    user_id=permission.user_id,
                    request_id=spec.request_id,
                    preview_id=preview.preview_id,
                    idempotency_key=spec.idempotency_key,
                    operation=spec.operation,
                    table_name=spec.table,
                    filters=spec.filters,
                    changes=spec.changes,
                    before_values={field: before.get(field) for field in spec.changes},
                    after_values={field: after.get(field) for field in spec.changes},
                    decision="APPROVED",
                    status="SUCCESS",
                    affected_rows=affected_rows,
                    data_version=preview.data_version,
                    permission_policy_version=permission.policy_version,
                )
            except RuntimeAgentError:
                raise
            except Exception as exc:
                raise RuntimeAgentError(
                    "MUTATION_EXECUTION_FAILED",
                    "The mutation audit could not be recorded",
                ) from exc
        return f"audit_{uuid4().hex[:16]}"


def _target(spec: MutationSpec) -> str:
    key, value = next(iter(spec.filters.items())) if spec.filters else ("", "")
    return f"{spec.table}.{key}={value}" if key else spec.table


def _row_version(table: str, row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{table}_{digest}"


def _commit_key(idempotency_key: str) -> str:
    return f"mutation-commit:{idempotency_key}"
