from datetime import datetime, timedelta, timezone

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.memory import ArtifactStore, CheckpointStore, SQLAlchemyCheckpointStore, UserMemoryStore
from backend.app.models import AgentState, PermissionContext, RunStatus


def test_checkpoint_optimistic_lock_and_artifact_revalidation(tmp_path):
    store = CheckpointStore(str(tmp_path / "checkpoint.sqlite"))
    state = AgentState(thread_id="t", request_id="r", user_id="u")
    first = store.save(state, expected_state_version=-1)
    state.status = RunStatus.WAITING_FOR_USER
    second = store.save(state, expected_state_version=first.state_version)
    assert second.state_version == 1
    with pytest.raises(RuntimeAgentError, match="changed"):
        store.save(state, expected_state_version=0)
    restored = CheckpointStore(str(tmp_path / "checkpoint.sqlite"))
    assert restored.load("t") is not None
    permission = PermissionContext(user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["s"], policy_version="p")
    artifacts = ArtifactStore()
    spec = artifacts.create(owner_user_id="u", conversation_id="t", artifact_type="RESULT_TABLE",
        payload=[{"x": 1}], permission=permission, catalog_version="catalog_v1")
    assert artifacts.get(spec.artifact_id, user_id="u", permission=permission, catalog_version="catalog_v1")[0]["x"] == 1
    stale = permission.model_copy(update={"policy_version": "p2"})
    with pytest.raises(RuntimeAgentError, match="expired"):
        artifacts.get(spec.artifact_id, user_id="u", permission=stale, catalog_version="catalog_v1")


def test_long_term_memory_requires_confirmation():
    memory = UserMemoryStore()
    with pytest.raises(RuntimeAgentError):
        memory.put("u", "timezone", "Asia/Shanghai")
    memory.put("u", "timezone", "Asia/Shanghai", confirmed=True)
    assert memory.get("u", "timezone") == "Asia/Shanghai"


def test_sqlalchemy_checkpoint_adapter_persists(tmp_path):
    store = SQLAlchemyCheckpointStore(f"sqlite:///{tmp_path / 'sql-checkpoint.sqlite'}")
    state = AgentState(thread_id="sql-t", request_id="sql-r", user_id="u")
    store.save(state, expected_state_version=-1)
    assert store.load("sql-t").thread_id == "sql-t"
