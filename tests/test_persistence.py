import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.models import AgentState, ArtifactType, PermissionContext, RunStatus, ScopeMode
from backend.app.repositories.runtime import RuntimePersistence


def permission(user_id="u_1", version="policy_v1"):
    return PermissionContext(user_id=user_id, roles=["USER"], scope_mode=ScopeMode.ALLOWLIST,
                             allowed_shop_ids=["shop_1"], policy_version=version)


def test_runtime_persistence_enforces_versions_ownership_and_artifact_revalidation(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path/'runtime.db'}", create_schema=True)
    state = AgentState(thread_id="thread_1", request_id="req_1", user_id="u_1")
    first = store.save_checkpoint(state, expected_state_version=-1, idempotency_key="node:req_1:first")
    state.status = RunStatus.SUCCEEDED
    second = store.save_checkpoint(state, expected_state_version=first.state_version,
                                   idempotency_key="node:req_1:second")
    assert second.state_version == 1
    history = store.checkpoints_for_thread("thread_1")
    assert [item.checkpoint_id for item in history] == [
        first.checkpoint_id, second.checkpoint_id]
    assert history[1].parent_checkpoint_id == history[0].checkpoint_id
    assert store.save_checkpoint(
        state, expected_state_version=-1,
        idempotency_key="node:req_1:second",
    ).checkpoint_id == second.checkpoint_id
    assert len(store.checkpoints_for_thread("thread_1")) == 2
    with pytest.raises(RuntimeAgentError, match="state version"):
        store.save_checkpoint(state, expected_state_version=0, idempotency_key="node:req_1:stale")

    store.append_message("thread_1", "u_1", "user", "昨天 GMV")
    store.append_message("thread_1", "u_1", "assistant", "查询完成")
    detail = store.thread_detail("thread_1", "u_1")
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
    assert store.list_threads("u_1")[0]["title"] == "昨天 GMV"

    result_id = store.save_result("u_1", [{"gmv": 10}, {"gmv": 20}])
    assert store.page_result(result_id, "u_1", 1, 1)["rows"] == [{"gmv": 20}]
    with pytest.raises(RuntimeAgentError):
        store.page_result(result_id, "u_2", 0, 10)

    artifact = store.create_artifact(owner_user_id="u_1", conversation_id="thread_1",
        artifact_type=ArtifactType.RESULT_TABLE, payload={"result_id": result_id},
        permission=permission(), catalog_version="catalog_v1", source_result_ids=[result_id])
    assert store.get_artifact(artifact.artifact_id, user_id="u_1", permission=permission(),
                              catalog_version="catalog_v1")["result_id"] == result_id
    with pytest.raises(RuntimeAgentError, match="expired or no longer authorized"):
        store.get_artifact(artifact.artifact_id, user_id="u_1", permission=permission(version="policy_v2"),
                           catalog_version="catalog_v1")


def test_runtime_event_replay_is_owner_scoped_and_idempotency_is_stable(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path/'events.db'}", create_schema=True)
    first_id = store.append_event("req_1", "u_1", {"event": "run.started"})
    second_id = store.append_event("req_1", "u_1", {"event": "node.completed"})
    store.append_event("req_1", "u_2", {"event": "private"})
    assert store.events_after("req_1", "u_1", first_id) == [
        (second_id, {"event": "node.completed"})
    ]
    assert store.put_idempotent("request-result:u_1:req_1", {"status": "SUCCEEDED"}) == {"status": "SUCCEEDED"}
    assert store.put_idempotent("request-result:u_1:req_1", {"status": "FAILED"}) == {"status": "SUCCEEDED"}


def test_confirmed_user_preferences_are_versioned_and_persistent(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path/'preferences.db'}", create_schema=True)
    with pytest.raises(RuntimeAgentError, match="confirmation"):
        store.put_user_preference("u_1", "timezone", "UTC", confirmed=False)
    first = store.put_user_preference("u_1", "timezone", "UTC", confirmed=True)
    second = store.put_user_preference("u_1", "timezone", "Asia/Shanghai", confirmed=True)
    assert first["version"] == 1
    assert second["version"] == 2
    assert second["memory_id"] == first["memory_id"]
    reopened = RuntimePersistence(url=f"sqlite:///{tmp_path/'preferences.db'}")
    assert reopened.user_preferences("u_1") == {"timezone": "Asia/Shanghai"}
