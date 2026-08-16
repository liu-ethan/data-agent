"""Checkpoints, artifacts, references and safe prompt projections."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..errors import RuntimeAgentError
from ..models import (AgentState, ArtifactSpec, ArtifactType, Checkpoint, Interrupt,
                     PermissionContext, RunStatus)
from ..testing_adapters import ResultRepository


class CheckpointStore:
    """Optimistic-locking store; the interface is compatible with a MySQL adapter."""

    def __init__(self, path: str | None = None) -> None:
        self._states: dict[str, AgentState] = {}
        self._checkpoints: dict[str, Checkpoint] = {}
        self._idempotent: dict[str, Any] = {}
        self._database = sqlite3.connect(path, check_same_thread=False) if path else None
        if self._database:
            self._database.executescript("""
              CREATE TABLE IF NOT EXISTS runtime_states(thread_id TEXT PRIMARY KEY, state_json TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS runtime_checkpoints(thread_id TEXT PRIMARY KEY, checkpoint_json TEXT NOT NULL);
            """)
            for thread_id, state_json in self._database.execute("SELECT thread_id,state_json FROM runtime_states"):
                self._states[thread_id] = AgentState.model_validate(json.loads(state_json))
            for thread_id, checkpoint_json in self._database.execute("SELECT thread_id,checkpoint_json FROM runtime_checkpoints"):
                checkpoint = Checkpoint.model_validate(json.loads(checkpoint_json))
                self._checkpoints[thread_id] = checkpoint
            self._database.commit()

    def save(self, state: AgentState, *, expected_state_version: int | None = None,
             idempotency_key: str | None = None) -> Checkpoint:
        current = self._states.get(state.thread_id)
        current_checkpoint = self._checkpoints.get(state.thread_id)
        current_version = current_checkpoint.state_version if current_checkpoint else -1
        if expected_state_version is not None and current_version != expected_state_version:
            raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
        if idempotency_key and idempotency_key in self._idempotent:
            return self._idempotent[idempotency_key]
        now = datetime.now(timezone.utc)
        checkpoint = Checkpoint(checkpoint_id=f"ckpt_{uuid4().hex[:12]}", thread_id=state.thread_id,
            state_version=current_version + 1, parent_checkpoint_id=current_checkpoint.checkpoint_id if current_checkpoint else None,
            status=state.status, serialized_state_ref=f"state:{state.thread_id}:{current_version + 1}",
            idempotency_key=idempotency_key or f"checkpoint:{state.request_id}:{current_version + 1}",
            created_at=now, updated_at=now)
        self._states[state.thread_id] = AgentState.model_validate(state.model_dump())
        self._checkpoints[state.request_id] = checkpoint
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._checkpoints[state.thread_id] = checkpoint
        if self._database:
            self._database.execute("INSERT OR REPLACE INTO runtime_states(thread_id,state_json) VALUES (?,?)",
                                   (state.thread_id, json.dumps(state.model_dump(mode="json"))))
            self._database.execute("INSERT OR REPLACE INTO runtime_checkpoints(thread_id,checkpoint_json) VALUES (?,?)",
                                   (state.thread_id, checkpoint.model_dump_json()))
            self._database.commit()
        if idempotency_key:
            self._idempotent[idempotency_key] = checkpoint
        return checkpoint

    def load(self, thread_id: str) -> AgentState | None:
        state = self._states.get(thread_id)
        return AgentState.model_validate(state.model_dump()) if state else None

    def checkpoint(self, thread_id: str) -> Checkpoint | None:
        return self._checkpoints.get(thread_id)

    def resume_guard(self, thread_id: str, user_id: str, expected_state_version: int,
                     client_request_id: str) -> Any | None:
        if client_request_id in self._idempotent:
            return self._idempotent[client_request_id]
        state = self._states.get(thread_id)
        checkpoint = self._checkpoints.get(thread_id)
        if not state or not checkpoint or state.status != RunStatus.WAITING_FOR_USER:
            raise RuntimeAgentError("INTERRUPT_INVALID", "thread is not waiting for user input")
        if state.user_id != user_id:
            raise RuntimeAgentError("PERMISSION_DENIED", "interrupt owner does not match")
        if checkpoint.state_version != expected_state_version:
            raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
        return None


class SQLAlchemyCheckpointStore:
    """Persistent checkpointer adapter for MySQL (and SQLite integration tests)."""

    def __init__(self, url: str) -> None:
        from sqlalchemy import create_engine, text
        self.engine = create_engine(url, future=True)
        with self.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS runtime_checkpoints (
                  thread_id VARCHAR(255) PRIMARY KEY,
                  state_version INTEGER NOT NULL,
                  state_json TEXT NOT NULL,
                  checkpoint_json TEXT NOT NULL,
                  idempotency_key VARCHAR(255) NOT NULL
                )
            """))

    def save(self, state: AgentState, *, expected_state_version: int | None = None,
             idempotency_key: str | None = None) -> Checkpoint:
        from sqlalchemy import text
        with self.engine.begin() as connection:
            row = connection.execute(text("SELECT state_version,checkpoint_json FROM runtime_checkpoints WHERE thread_id=:thread_id"),
                                     {"thread_id": state.thread_id}).mappings().first()
            current = int(row["state_version"]) if row else -1
            if expected_state_version is not None and current != expected_state_version:
                raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
            now = datetime.now(timezone.utc)
            checkpoint = Checkpoint(checkpoint_id=f"ckpt_{uuid4().hex[:12]}", thread_id=state.thread_id,
                state_version=current + 1, parent_checkpoint_id=None, status=state.status,
                serialized_state_ref=f"sqlstate:{state.thread_id}:{current + 1}",
                idempotency_key=idempotency_key or f"checkpoint:{state.thread_id}:{current + 1}",
                created_at=now, updated_at=now)
            params = {"thread_id": state.thread_id, "state_version": checkpoint.state_version,
                      "state_json": json.dumps(state.model_dump(mode="json")),
                      "checkpoint_json": checkpoint.model_dump_json(),
                      "idempotency_key": checkpoint.idempotency_key}
            if row:
                connection.execute(text("""UPDATE runtime_checkpoints SET state_version=:state_version,
                    state_json=:state_json, checkpoint_json=:checkpoint_json, idempotency_key=:idempotency_key
                    WHERE thread_id=:thread_id"""), params)
            else:
                connection.execute(text("""INSERT INTO runtime_checkpoints(thread_id,state_version,state_json,checkpoint_json,idempotency_key)
                    VALUES (:thread_id,:state_version,:state_json,:checkpoint_json,:idempotency_key)"""), params)
            return checkpoint

    def load(self, thread_id: str) -> AgentState | None:
        from sqlalchemy import text
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT state_json FROM runtime_checkpoints WHERE thread_id=:thread_id"),
                                     {"thread_id": thread_id}).mappings().first()
        return AgentState.model_validate(json.loads(row["state_json"])) if row else None


class ArtifactStore:
    def __init__(self, results: ResultRepository | None = None) -> None:
        self.results = results or ResultRepository()
        self.specs: dict[str, ArtifactSpec] = {}
        self.payloads: dict[str, Any] = {}

    def create(self, *, owner_user_id: str, conversation_id: str, artifact_type: ArtifactType,
               payload: Any, permission: PermissionContext, catalog_version: str,
               source_result_ids: list[str] | None = None, source_ref: str | None = None,
               ttl_days: int = 30) -> ArtifactSpec:
        now = datetime.now(timezone.utc)
        artifact_id = f"artifact_{uuid4().hex[:12]}"
        payload_ref = f"payload_{uuid4().hex[:12]}"
        spec = ArtifactSpec(artifact_id=artifact_id, conversation_id=conversation_id,
            owner_user_id=owner_user_id, type=artifact_type, source_result_ids=source_result_ids or [],
            source_ref=source_ref, permission_policy_version=permission.policy_version,
            catalog_version=catalog_version, created_at=now, expires_at=now + timedelta(days=ttl_days),
            payload_ref=payload_ref)
        self.specs[artifact_id] = spec
        self.payloads[payload_ref] = payload
        return spec

    def get(self, artifact_id: str, *, user_id: str, permission: PermissionContext,
            catalog_version: str, now: datetime | None = None) -> Any:
        spec = self.specs.get(artifact_id)
        now = now or datetime.now(timezone.utc)
        if (not spec or spec.owner_user_id != user_id or spec.permission_policy_version != permission.policy_version
                or spec.catalog_version != catalog_version or spec.expires_at <= now):
            raise RuntimeAgentError("ARTIFACT_STALE", "artifact is expired or no longer authorized")
        return self.payloads[spec.payload_ref]

    def csv(self, artifact_id: str, **kwargs: Any) -> str:
        payload = self.get(artifact_id, **kwargs)
        if not isinstance(payload, list):
            raise RuntimeAgentError("ARTIFACT_STALE", "artifact is not a tabular payload")
        import csv
        import io
        output = io.StringIO()
        if not payload:
            return ""
        writer = csv.DictWriter(output, fieldnames=list(payload[0]))
        writer.writeheader(); writer.writerows(payload)
        return output.getvalue()


class ReferenceResolver:
    def resolve(self, text: str, artifacts: list[ArtifactSpec]) -> tuple[str | None, str | None]:
        candidates = [a for a in artifacts if a.type in {ArtifactType.FIELD_LIST, ArtifactType.RESULT_TABLE}]
        if not candidates:
            return None, "no prior artifact is available"
        ordinal_match = None
        import re
        match = re.search(r"第\s*(\d+)\s*(?:个|项|列|字段)", text)
        if match:
            ordinal_match = int(match.group(1))
        if ordinal_match and ordinal_match <= len(candidates):
            return candidates[ordinal_match - 1].artifact_id, None
        if any(term in text for term in ("刚才", "上一个", "上一张", "结果")):
            return candidates[-1].artifact_id, None
        return None, "reference is ambiguous"


class PromptContextBuilder:
    def build(self, *, state: AgentState, context: Any | None = None,
              summary: dict[str, Any] | None = None) -> dict[str, Any]:
        # This projection deliberately contains references and bounded metadata,
        # never the result repository's complete rows.
        return {"task_frame": state.task_frame.model_dump() if state.task_frame else None,
                "grounded_context_id": state.grounded_context_id,
                "coverage": state.coverage,
                "latest_observation": state.latest_observation.model_dump(exclude={"summary"})
                    if state.latest_observation else None,
                "result_ids": state.result_ids,
                "summary": summary or {},
                "context": context.model_dump() if context else None}


class UserMemoryStore:
    ALLOWED_KEYS = {"timezone", "default_shop_id", "chart_preference", "number_format"}

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], dict[str, Any]] = {}

    def put(self, user_id: str, key: str, value: Any, *, source: str = "USER_CONFIRMED",
            confirmed: bool = False) -> dict[str, Any]:
        if key not in self.ALLOWED_KEYS or not confirmed:
            raise RuntimeAgentError("WRITE_FORBIDDEN", "long-term memory requires an allowed confirmed key")
        item = {"memory_id": f"memory_{uuid4().hex[:12]}", "user_id": user_id, "memory_key": key,
                "value": value, "source": source, "version": 1, "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": None, "created_at": datetime.now(timezone.utc).isoformat()}
        old = self.values.get((user_id, key))
        if old:
            item["version"] = old["version"] + 1
        self.values[(user_id, key)] = item
        return item

    def get(self, user_id: str, key: str) -> Any | None:
        item = self.values.get((user_id, key))
        return item["value"] if item else None
