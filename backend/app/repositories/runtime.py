"""MySQL-backed runtime state, result and artifact persistence.

This module deliberately keeps persistence independent from LangGraph's own
serializer so a checkpoint remains inspectable and recoverable after a library
upgrade.  Every mutable record is written transactionally with an optimistic
version check.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    select,
    update,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..auth import hash_password
from ..errors import RuntimeAgentError
from ..models import (
    AgentState,
    ArtifactSpec,
    ArtifactType,
    Checkpoint,
    PermissionContext,
)


def mysql_url(mysql: dict[str, Any], account_name: str = "control", *,
               database: str | None = None) -> URL:
    """Build a MySQL URL bound to the requested database.

    Defaults to the system database; callers that need the business
    database (e.g. the read-only data gateway) pass ``database`` explicitly.
    """
    account = mysql.get("accounts", {}).get(account_name, {})
    target = database or mysql.get("system_database") or mysql.get("database")
    return URL.create("mysql+pymysql", username=account.get("username"), password=account.get("password"),
                      host=mysql.get("host"), port=int(mysql.get("port", 3306)), database=target,
                      query={"charset": mysql.get("charset", "utf8mb4")})


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _privilege_denied(exc: SQLAlchemyError) -> bool:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "args", [None])[0] == 1142


class RuntimePersistence:
    def __init__(self, mysql: dict[str, Any] | None = None, *, url: str | None = None,
                 create_schema: bool = False, account_name: str = "control") -> None:
        if url:
            self.engine = create_engine(url, future=True, pool_pre_ping=True)
        elif mysql:
            self.engine = create_engine(mysql_url(mysql, account_name), future=True, pool_pre_ping=True,
                                        pool_size=int(mysql.get("pool_size", 10)),
                                        max_overflow=int(mysql.get("max_overflow", 20)))
        else:
            raise ValueError("RuntimePersistence requires a database URL")
        self.metadata = MetaData()
        self.app_users = Table("app_users", self.metadata,
            Column("user_id", String(255), primary_key=True),
            Column("password_hash", String(255), nullable=True),
            Column("role_name", String(32), nullable=False),
            Column("active", Integer, nullable=False, default=1), Column("policy_version", String(128), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
        self.app_user_shop_scopes = Table("app_user_shop_scopes", self.metadata,
            Column("user_id", String(255), primary_key=True), Column("shop_id", String(64), primary_key=True),
            Column("policy_version", String(128), nullable=False))
        self.checkpoints = Table("runtime_checkpoints", self.metadata,
            Column("thread_id", String(255), primary_key=True), Column("state_version", Integer, nullable=False),
            Column("state_json", Text, nullable=False), Column("checkpoint_json", Text, nullable=False),
            Column("idempotency_key", String(255), nullable=False, unique=True), Column("updated_at", DateTime(timezone=True), nullable=False))
        self.checkpoint_history = Table("runtime_checkpoint_history", self.metadata,
            Column("checkpoint_id", String(64), primary_key=True),
            Column("thread_id", String(255), nullable=False, index=True),
            Column("state_version", Integer, nullable=False),
            Column("parent_checkpoint_id", String(64), nullable=True),
            Column("status", String(32), nullable=False),
            Column("state_json", Text, nullable=False),
            Column("checkpoint_json", Text, nullable=False),
            Column("idempotency_key", String(255), nullable=False, unique=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("thread_id", "state_version",
                             name="uq_checkpoint_history_thread_version"))
        self.idempotency = Table("runtime_idempotency", self.metadata,
            Column("key", String(255), primary_key=True), Column("value_json", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
        self.events = Table("runtime_events", self.metadata,
            Column("event_id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
            Column("request_id", String(255), nullable=False, index=True),
            Column("owner_user_id", String(255), nullable=False, index=True),
            Column("event_json", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
        self.results = Table("runtime_results", self.metadata,
            Column("result_id", String(64), primary_key=True), Column("owner_user_id", String(255), nullable=False),
            Column("rows_json", Text, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False))
        self.artifacts = Table("conversation_artifacts", self.metadata,
            Column("artifact_id", String(64), primary_key=True), Column("owner_user_id", String(255), nullable=False),
            Column("spec_json", Text, nullable=False), Column("payload_json", Text, nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False))
        self.messages = Table("conversation_messages", self.metadata,
            Column("message_id", String(64), primary_key=True), Column("thread_id", String(255), nullable=False, index=True),
            Column("user_id", String(255), nullable=False), Column("role", String(16), nullable=False), Column("content", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False))
        self.user_memories = Table("user_memories", self.metadata,
            Column("memory_id", String(64), primary_key=True),
            Column("user_id", String(255), nullable=False, index=True),
            Column("memory_key", String(64), nullable=False),
            Column("value_json", Text, nullable=False),
            Column("source", String(32), nullable=False),
            Column("version", Integer, nullable=False),
            Column("confirmed_at", DateTime(timezone=True), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("user_id", "memory_key", name="uq_user_memory_key"))
        self.memory_history = Table("user_memory_history", self.metadata,
            Column("history_id", String(64), primary_key=True),
            Column("memory_id", String(64), nullable=False, index=True),
            Column("user_id", String(255), nullable=False, index=True),
            Column("memory_key", String(64), nullable=False),
            Column("old_value_json", Text, nullable=True),
            Column("new_value_json", Text, nullable=False),
            Column("source", String(32), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False))
        self.invite_codes = Table("invite_codes", self.metadata,
            Column("code", String(64), primary_key=True),
            Column("role_name", String(32), nullable=False),
            Column("max_uses", Integer, nullable=False, default=1),
            Column("used_count", Integer, nullable=False, default=0),
            Column("policy_version", String(128), nullable=False),
            Column("created_by", String(255), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=True),
            Column("active", Integer, nullable=False, default=1))
        self.thread_titles = Table("thread_titles", self.metadata,
            Column("thread_id", String(255), primary_key=True),
            Column("title", String(64), nullable=False),
            Column("generated_at", DateTime(timezone=True), nullable=False))
        self.mutation_audits_table = Table("mutation_audit", self.metadata,
            Column("audit_id", String(64), primary_key=True),
            Column("user_id", String(255), nullable=False, index=True),
            Column("request_id", String(255), nullable=False),
            Column("preview_id", String(64), nullable=False),
            Column("idempotency_key", String(255), nullable=False, index=True),
            Column("operation", String(16), nullable=False),
            Column("table_name", String(64), nullable=False),
            Column("filters_json", Text, nullable=False),
            Column("changes_json", Text, nullable=False),
            Column("before_json", Text, nullable=False),
            Column("after_json", Text, nullable=False),
            Column("decision", String(32), nullable=False),
            Column("status", String(32), nullable=False),
            Column("affected_rows", Integer, nullable=False),
            Column("data_version", String(128), nullable=False),
            Column("permission_policy_version", String(128), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False))
        if create_schema:
            self.metadata.create_all(self.engine)

    def healthcheck(self) -> bool:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True

    def login_identity(self, account: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    self.app_users.c.user_id,
                    self.app_users.c.password_hash,
                    self.app_users.c.role_name,
                    self.app_users.c.active,
                ).where(self.app_users.c.user_id == account)
            ).mappings().first()
        return dict(row) if row else None

    def save_checkpoint(self, state: AgentState, *, expected_state_version: int | None = None,
                        idempotency_key: str | None = None,
                        checkpoint_id: str | None = None) -> Checkpoint:
        key = idempotency_key or f"checkpoint:{state.thread_id}:{state.request_id}:{state.status.value}"
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            existing_idempotent = connection.execute(select(self.idempotency.c.value_json).where(self.idempotency.c.key == key)).scalar_one_or_none()
            if existing_idempotent:
                return Checkpoint.model_validate(json.loads(existing_idempotent))
            row = connection.execute(select(self.checkpoints).where(self.checkpoints.c.thread_id == state.thread_id)).mappings().first()
            current = int(row["state_version"]) if row else -1
            if expected_state_version is not None and current != expected_state_version:
                raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
            checkpoint = Checkpoint(checkpoint_id=checkpoint_id or f"ckpt_{uuid4().hex[:16]}", thread_id=state.thread_id,
                state_version=current + 1, parent_checkpoint_id=Checkpoint.model_validate(json.loads(row["checkpoint_json"])).checkpoint_id if row else None,
                status=state.status, serialized_state_ref=f"state:{state.thread_id}:{current + 1}", idempotency_key=key,
                created_at=now, updated_at=now)
            values = {"thread_id": state.thread_id, "state_version": checkpoint.state_version,
                "state_json": json.dumps(state.model_dump(mode="json")), "checkpoint_json": checkpoint.model_dump_json(),
                "idempotency_key": key, "updated_at": now}
            if row:
                changed = connection.execute(self.checkpoints.update().where(and_(self.checkpoints.c.thread_id == state.thread_id, self.checkpoints.c.state_version == current)).values(**values)).rowcount
                if not changed: raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
            else: connection.execute(self.checkpoints.insert().values(**values))
            connection.execute(self.checkpoint_history.insert().values(
                checkpoint_id=checkpoint.checkpoint_id,
                thread_id=checkpoint.thread_id,
                state_version=checkpoint.state_version,
                parent_checkpoint_id=checkpoint.parent_checkpoint_id,
                status=checkpoint.status.value,
                state_json=values["state_json"],
                checkpoint_json=values["checkpoint_json"],
                idempotency_key=checkpoint.idempotency_key,
                created_at=checkpoint.created_at,
                updated_at=checkpoint.updated_at,
            ))
            connection.execute(self.idempotency.insert().values(key=key, value_json=checkpoint.model_dump_json(), created_at=now))
            return checkpoint

    def load_state(self, thread_id: str) -> AgentState | None:
        with self.engine.connect() as connection:
            payload = connection.execute(select(self.checkpoints.c.state_json).where(self.checkpoints.c.thread_id == thread_id)).scalar_one_or_none()
        return AgentState.model_validate(json.loads(payload)) if payload else None

    def checkpoint(self, thread_id: str) -> Checkpoint | None:
        with self.engine.connect() as connection:
            payload = connection.execute(select(self.checkpoints.c.checkpoint_json).where(self.checkpoints.c.thread_id == thread_id)).scalar_one_or_none()
        return Checkpoint.model_validate(json.loads(payload)) if payload else None

    def checkpoints_for_thread(self, thread_id: str) -> list[Checkpoint]:
        """Return the immutable super-step chain for recovery and audit."""
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(self.checkpoint_history.c.checkpoint_json)
                .where(self.checkpoint_history.c.thread_id == thread_id)
                .order_by(self.checkpoint_history.c.state_version)
            ).scalars().all()
        return [Checkpoint.model_validate_json(payload) for payload in payloads]

    def append_message(self, thread_id: str, user_id: str, role: str, content: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.messages.insert().values(message_id=f"msg_{uuid4().hex[:16]}", thread_id=thread_id, user_id=user_id, role=role, content=content, created_at=datetime.now(UTC)))

    def append_event(self, request_id: str, owner_user_id: str, event: dict[str, Any]) -> int:
        with self.engine.begin() as connection:
            result = connection.execute(self.events.insert().values(request_id=request_id,
                owner_user_id=owner_user_id,
                event_json=json.dumps(event, default=str, ensure_ascii=False), created_at=datetime.now(UTC)))
            inserted = result.inserted_primary_key
            assert inserted is not None, "append_event requires an auto-increment primary key"
            return int(inserted[0])

    def events_after(self, request_id: str, owner_user_id: str, after_id: int = 0, limit: int = 100) -> list[tuple[int, dict[str, Any]]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.events.c.event_id, self.events.c.event_json)
                .where(and_(self.events.c.request_id == request_id,
                            self.events.c.owner_user_id == owner_user_id,
                            self.events.c.event_id > after_id))
                .order_by(self.events.c.event_id).limit(limit)).all()
        return [(int(row.event_id), json.loads(row.event_json)) for row in rows]

    def get_idempotent(self, key: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            value = connection.execute(select(self.idempotency.c.value_json).where(self.idempotency.c.key == key)).scalar_one_or_none()
        return json.loads(value) if value else None

    def put_idempotent(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(value, default=str, ensure_ascii=False)
        try:
            with self.engine.begin() as connection:
                connection.execute(self.idempotency.insert().values(key=key, value_json=payload,
                    created_at=datetime.now(UTC)))
            return value
        except IntegrityError:
            existing = self.get_idempotent(key)
            if existing is None: raise
            return existing

    def recent_messages(self, thread_id: str, limit: int = 8) -> list[dict[str, str]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.messages.c.role, self.messages.c.content).where(self.messages.c.thread_id == thread_id).order_by(self.messages.c.created_at.desc()).limit(limit)).mappings().all()
        return [dict(row) for row in reversed(rows)]

    def user_preferences(self, user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.user_memories.c.memory_key,
                self.user_memories.c.value_json, self.user_memories.c.expires_at)
                .where(self.user_memories.c.user_id == user_id)).mappings().all()
        return {str(row["memory_key"]): json.loads(row["value_json"]) for row in rows
                if row["expires_at"] is None or _utc(row["expires_at"]) > now}

    def put_user_preference(self, user_id: str, key: str, value: Any, *,
                            confirmed: bool, source: str = "USER_CONFIRMED") -> dict[str, Any]:
        if not confirmed:
            raise RuntimeAgentError("MEMORY_CONFIRMATION_REQUIRED",
                                    "long-term preferences require explicit confirmation")
        now = datetime.now(UTC)
        payload = json.dumps(value, ensure_ascii=False)
        history: dict[str, Any] | None = None
        try:
            with self.engine.begin() as connection:
                row = connection.execute(select(self.user_memories).where(and_(
                    self.user_memories.c.user_id == user_id,
                    self.user_memories.c.memory_key == key))).mappings().first()
                if row:
                    version = int(row["version"]) + 1
                    connection.execute(self.user_memories.update().where(and_(
                        self.user_memories.c.user_id == user_id,
                        self.user_memories.c.memory_key == key)).values(
                        value_json=payload, source=source, version=version,
                        confirmed_at=now, updated_at=now))
                    memory_id = str(row["memory_id"])
                    history = {
                        "memory_id": memory_id, "user_id": user_id, "memory_key": key,
                        "old_value_json": row["value_json"], "new_value_json": payload,
                        "source": source, "created_at": now,
                    }
                else:
                    version, memory_id = 1, f"memory_{uuid4().hex[:16]}"
                    connection.execute(self.user_memories.insert().values(
                        memory_id=memory_id, user_id=user_id, memory_key=key,
                        value_json=payload, source=source, version=version,
                        confirmed_at=now, expires_at=None, created_at=now, updated_at=now))
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "MEMORY_WRITE_FAILED", "the confirmed preference could not be saved"
            ) from exc
        if history is not None:
            self._append_memory_history(history)
        return {"memory_id": memory_id, "key": key, "value": value,
                "source": source, "version": version,
                "confirmed_at": now.isoformat()}

    def _append_memory_history(self, history: dict[str, Any]) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(self.memory_history.insert().values(
                    history_id=f"memhist_{uuid4().hex[:16]}", **history))
        except SQLAlchemyError as exc:
            if _privilege_denied(exc):
                return
            raise RuntimeAgentError(
                "MEMORY_WRITE_FAILED", "the confirmed preference could not be saved"
            ) from exc

    def user_memory_history(self, user_id: str, key: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.memory_history)
                .where(and_(
                    self.memory_history.c.user_id == user_id,
                    self.memory_history.c.memory_key == key))
                .order_by(self.memory_history.c.created_at)
            ).mappings().all()
        history = []
        for row in rows:
            history.append({
                "memory_id": row["memory_id"],
                "old_value": json.loads(row["old_value_json"]) if row["old_value_json"] else None,
                "new_value": json.loads(row["new_value_json"]),
                "source": row["source"],
                "created_at": _utc(row["created_at"]).isoformat(),
            })
        return history

    def list_threads(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.messages.c.thread_id, self.messages.c.role,
                self.messages.c.content, self.messages.c.created_at)
                .where(self.messages.c.user_id == user_id)
                .order_by(self.messages.c.created_at.desc()).limit(max(1, limit * 8))).mappings().all()
            generated_rows = connection.execute(select(self.thread_titles.c.thread_id,
                self.thread_titles.c.title, self.thread_titles.c.generated_at)).mappings().all()
        titles_by_thread = {row["thread_id"]: row["title"] for row in generated_rows}
        threads: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = threads.setdefault(row["thread_id"], {"thread_id": row["thread_id"],
                "title": titles_by_thread.get(row["thread_id"], "未命名分析"),
                "updated_at": _utc(row["created_at"]).isoformat()})
            if row["thread_id"] in titles_by_thread:
                item["title"] = titles_by_thread[row["thread_id"]]
            elif row["role"] == "user" and item["title"] == "未命名分析":
                item["title"] = str(row["content"])[:80]
        return list(threads.values())[:limit]

    def delete_thread(self, thread_id: str, user_id: str) -> None:
        state = self.load_state(thread_id)
        try:
            with self.engine.begin() as connection:
                owned = connection.execute(select(self.messages.c.message_id).where(and_(
                    self.messages.c.thread_id == thread_id, self.messages.c.user_id == user_id,
                )).limit(1)).first()
                if state is None and owned is None:
                    raise KeyError(thread_id)
                if state is not None and state.user_id != user_id:
                    raise RuntimeAgentError("PERMISSION_DENIED", "thread owner does not match")
                connection.execute(self.messages.delete().where(and_(
                    self.messages.c.thread_id == thread_id, self.messages.c.user_id == user_id)))
                if state is None or state.user_id == user_id:
                    connection.execute(self.checkpoints.delete().where(
                        self.checkpoints.c.thread_id == thread_id))
                    connection.execute(self.checkpoint_history.delete().where(
                        self.checkpoint_history.c.thread_id == thread_id))
        except RuntimeAgentError:
            raise
        except KeyError:
            raise
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "THREAD_DELETE_FAILED", "the conversation could not be deleted"
            ) from exc
        self._delete_thread_title(thread_id)

    def _delete_thread_title(self, thread_id: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(self.thread_titles.delete().where(
                    self.thread_titles.c.thread_id == thread_id))
        except SQLAlchemyError as exc:
            if _privilege_denied(exc):
                return
            raise RuntimeAgentError(
                "THREAD_DELETE_FAILED", "the conversation could not be deleted"
            ) from exc

    def thread_detail(self, thread_id: str, user_id: str) -> dict[str, Any]:
        state = self.load_state(thread_id)
        if not state: raise KeyError(thread_id)
        if state.user_id != user_id: raise RuntimeAgentError("PERMISSION_DENIED", "thread owner does not match")
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.messages.c.role, self.messages.c.content,
                self.messages.c.created_at).where(and_(self.messages.c.thread_id == thread_id,
                self.messages.c.user_id == user_id)).order_by(self.messages.c.created_at)).mappings().all()
        checkpoint = self.checkpoint(thread_id)
        return {"thread_id": thread_id, "status": state.status.value,
            "messages": [{"role": row["role"], "content": row["content"],
                          "created_at": _utc(row["created_at"]).isoformat()} for row in rows],
            "result_ids": state.result_ids, "artifact_ids": state.artifact_ids,
            "interrupt": state.pending_interrupt.model_dump(mode="json") if state.pending_interrupt else None,
            "state_version": checkpoint.state_version if checkpoint else None}

    def save_result(self, owner_user_id: str, rows: list[dict[str, Any]], *, ttl_days: int = 30) -> str:
        result_id, now = f"result_{uuid4().hex[:16]}", datetime.now(UTC)
        with self.engine.begin() as connection:
            connection.execute(self.results.insert().values(result_id=result_id, owner_user_id=owner_user_id, rows_json=json.dumps(rows, default=str, ensure_ascii=False), created_at=now, expires_at=now + timedelta(days=ttl_days)))
        return result_id

    def page_result(self, result_id: str, user_id: str, offset: int, limit: int) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.results).where(self.results.c.result_id == result_id)).mappings().first()
        if not row: raise KeyError(result_id)
        if row["owner_user_id"] != user_id or _utc(row["expires_at"]) <= datetime.now(UTC): raise RuntimeAgentError("PERMISSION_DENIED", "result is not available")
        rows = json.loads(row["rows_json"])
        return {"result_id": result_id, "rows": rows[offset:offset + limit], "offset": offset, "limit": limit, "total": len(rows)}

    def create_artifact(self, *, owner_user_id: str, conversation_id: str, artifact_type: ArtifactType, payload: Any, permission: PermissionContext, catalog_version: str, source_result_ids: list[str] | None = None, source_ref: str | None = None, ttl_days: int = 30) -> ArtifactSpec:
        now, artifact_id, payload_ref = datetime.now(UTC), f"artifact_{uuid4().hex[:16]}", f"payload_{uuid4().hex[:16]}"
        spec = ArtifactSpec(artifact_id=artifact_id, conversation_id=conversation_id, owner_user_id=owner_user_id, type=artifact_type, source_result_ids=source_result_ids or [], source_ref=source_ref, permission_policy_version=permission.policy_version, catalog_version=catalog_version, created_at=now, expires_at=now + timedelta(days=ttl_days), payload_ref=payload_ref)
        with self.engine.begin() as connection:
            connection.execute(self.artifacts.insert().values(artifact_id=artifact_id, owner_user_id=owner_user_id, spec_json=spec.model_dump_json(), payload_json=json.dumps(payload, default=str, ensure_ascii=False), expires_at=spec.expires_at))
        return spec

    def get_artifact(self, artifact_id: str, *, user_id: str, permission: PermissionContext, catalog_version: str) -> Any:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.artifacts).where(self.artifacts.c.artifact_id == artifact_id)).mappings().first()
        if not row: raise RuntimeAgentError("ARTIFACT_STALE", "artifact is expired or no longer authorized")
        spec = ArtifactSpec.model_validate_json(row["spec_json"])
        if spec.owner_user_id != user_id or spec.permission_policy_version != permission.policy_version or spec.catalog_version != catalog_version or _utc(spec.expires_at) <= datetime.now(UTC):
            raise RuntimeAgentError("ARTIFACT_STALE", "artifact is expired or no longer authorized")
        return json.loads(row["payload_json"])

    def get_artifact_record(self, artifact_id: str, *, user_id: str,
                            permission: PermissionContext, catalog_version: str) -> dict[str, Any]:
        payload = self.get_artifact(artifact_id, user_id=user_id, permission=permission,
                                    catalog_version=catalog_version)
        with self.engine.connect() as connection:
            raw = connection.execute(select(self.artifacts.c.spec_json)
                .where(self.artifacts.c.artifact_id == artifact_id)).scalar_one()
        return {"spec": ArtifactSpec.model_validate_json(raw).model_dump(mode="json"),
                "payload": payload}

    def csv_result(self, result_id: str, user_id: str) -> str:
        page = self.page_result(result_id, user_id, 0, 10_000); rows = page["rows"]
        if not rows: return ""
        output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        return output.getvalue()

    def consume_invite_code(self, code: str, role: str) -> dict[str, Any] | None:
        """Atomically decrement the remaining uses for an active, unexpired invite.

        Returns the invite row on success, None if the code cannot be consumed.
        """
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            row = connection.execute(select(self.invite_codes)
                .where(self.invite_codes.c.code == code)).mappings().first()
            if not row:
                return None
            if not row["active"] or row["role_name"] != role:
                return None
            if row["used_count"] >= row["max_uses"]:
                return None
            expires_at = row["expires_at"]
            if expires_at is not None and _utc(expires_at) <= now:
                return None
            consumed = connection.execute(update(self.invite_codes)
                .where(and_(self.invite_codes.c.code == code,
                            self.invite_codes.c.used_count < self.invite_codes.c.max_uses,
                            self.invite_codes.c.active == 1))
                .values(used_count=self.invite_codes.c.used_count + 1)).rowcount
            if not consumed:
                return None
        return dict(row)

    def register_user(self, *, account: str, password: str, role: str,
                      policy_version: str) -> None:
        """Provision a fresh identity with a freshly hashed password.

        Raises ``RuntimeAgentError`` for duplicate accounts; invite consumption
        is the caller's responsibility and must happen before this call.
        """
        now = datetime.now(UTC)
        password_hash = hash_password(password)
        with self.engine.begin() as connection:
            existing = connection.execute(select(self.app_users.c.user_id)
                .where(self.app_users.c.user_id == account)).first()
            if existing is not None:
                raise RuntimeAgentError("ACCOUNT_TAKEN", "account already exists")
            connection.execute(self.app_users.insert().values(
                user_id=account, password_hash=password_hash,
                role_name=role, active=1, policy_version=policy_version,
                created_at=now, updated_at=now))

    def save_thread_title(self, thread_id: str, title: str) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            connection.execute(self.thread_titles.insert().prefix_with("IGNORE").values(
                thread_id=thread_id, title=title, generated_at=now))

    def load_thread_title(self, thread_id: str) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.thread_titles.c.title)
                .where(self.thread_titles.c.thread_id == thread_id)).first()
        return str(row[0]) if row else None

    def create_invite_code(self, *, code: str, role: str, max_uses: int,
                           policy_version: str, created_by: str,
                           expires_at: datetime | None) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.invite_codes.insert().values(
                code=code, role_name=role, max_uses=max_uses, used_count=0,
                policy_version=policy_version, created_by=created_by,
                created_at=datetime.now(UTC), expires_at=expires_at,
                active=1))


    def record_mutation_audit(
        self,
        *,
        user_id: str,
        request_id: str,
        preview_id: str,
        idempotency_key: str,
        operation: str,
        table_name: str,
        filters: dict[str, Any],
        changes: dict[str, Any],
        before_values: dict[str, Any],
        after_values: dict[str, Any],
        decision: str,
        status: str,
        affected_rows: int,
        data_version: str,
        permission_policy_version: str,
    ) -> str:
        audit_id = f"audit_{uuid4().hex[:16]}"
        try:
            with self.engine.begin() as connection:
                connection.execute(self.mutation_audits_table.insert().values(
                    audit_id=audit_id,
                    user_id=user_id,
                    request_id=request_id,
                    preview_id=preview_id,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    table_name=table_name,
                    filters_json=json.dumps(filters, ensure_ascii=False, default=str),
                    changes_json=json.dumps(changes, ensure_ascii=False, default=str),
                    before_json=json.dumps(before_values, ensure_ascii=False, default=str),
                    after_json=json.dumps(after_values, ensure_ascii=False, default=str),
                    decision=decision,
                    status=status,
                    affected_rows=affected_rows,
                    data_version=data_version,
                    permission_policy_version=permission_policy_version,
                    created_at=datetime.now(UTC),
                ))
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "MUTATION_EXECUTION_FAILED",
                "The mutation audit could not be recorded",
            ) from exc
        return audit_id

    def ensure_mutation_audit(self) -> None:
        try:
            with self.engine.connect() as connection:
                connection.execute(select(self.mutation_audits_table.c.audit_id).limit(1))
        except SQLAlchemyError as exc:
            raise RuntimeAgentError(
                "MUTATION_EXECUTION_FAILED",
                "mutation audit table is not available",
            ) from exc

    def mutation_audits(self, *, idempotency_key: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.mutation_audits_table)
                .where(self.mutation_audits_table.c.idempotency_key == idempotency_key)
                .order_by(self.mutation_audits_table.c.created_at)
            ).mappings().all()
        records = []
        for row in rows:
            records.append({
                "audit_id": row["audit_id"],
                "user_id": row["user_id"],
                "request_id": row["request_id"],
                "preview_id": row["preview_id"],
                "idempotency_key": row["idempotency_key"],
                "operation": row["operation"],
                "table_name": row["table_name"],
                "filters": json.loads(row["filters_json"]),
                "changes": json.loads(row["changes_json"]),
                "before_values": json.loads(row["before_json"]),
                "after_values": json.loads(row["after_json"]),
                "decision": row["decision"],
                "status": row["status"],
                "affected_rows": row["affected_rows"],
                "data_version": row["data_version"],
                "permission_policy_version": row["permission_policy_version"],
            })
        return records


class PersistentResultRepository:
    """Gateway-compatible facade over the durable runtime result table."""

    def __init__(self, persistence: RuntimePersistence) -> None:
        self.persistence = persistence

    def save(self, rows: list[dict[str, Any]], *, owner_user_id: str | None = None) -> str:
        if not owner_user_id:
            raise RuntimeError("result owner is required")
        return self.persistence.save_result(owner_user_id, rows)

    def page(self, result_id: str, *, user_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.persistence.page_result(result_id, user_id, offset, limit)

    def get(self, result_id: str) -> list[dict[str, Any]] | None:
        # Graph code must not use this to expose data.  It is kept only for
        # ResultAnalyzer, which has already established ownership.
        try:
            with self.persistence.engine.connect() as connection:
                row = connection.execute(select(self.persistence.results.c.rows_json).where(self.persistence.results.c.result_id == result_id)).scalar_one_or_none()
            return json.loads(row) if row else None
        except Exception:
            return None
