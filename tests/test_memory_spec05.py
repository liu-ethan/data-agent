"""Spec 05 acceptance tests for memory, interrupts and artifacts.

Covers the §8 invariants that leftover Codex stores do not prove:
ReferenceResolver field binding, PromptContextBuilder row exclusion,
rolling-summary source tags, explicit conditions beating long-term
preferences, Artifact/Checkpoint revalidation, and result_id-only
CSV / CHART_DSL payloads.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.api.app import _interrupt_resumable
from backend.app.errors import RuntimeAgentError
from backend.app.graph._task_understanding import understand_task
from backend.app.graph.nodes import response_node
from backend.app.memory import (
    PromptContextBuilder,
    ReferenceResolver,
    RollingSummaryBuilder,
    apply_preferences,
    is_long_term_preference_request,
)
from backend.app.models import (
    AgentState,
    ArtifactSpec,
    ArtifactType,
    CoverageStatus,
    GroundedContext,
    Intent,
    Interrupt,
    MutationSpec,
    PermissionContext,
    ResultObservation,
    ResultStatus,
    ResultSummary,
    RunStatus,
    ScopeMode,
    TaskFrame,
)
from backend.app.repositories.runtime import RuntimePersistence
from backend.app.testing import build_test_runtime


def _permission(user_id: str = "u_east_user", version: str = "policy_v18") -> PermissionContext:
    return PermissionContext(
        user_id=user_id,
        roles=["USER"],
        scope_mode=ScopeMode.ALLOWLIST,
        allowed_shop_ids=["shop_001", "shop_002"],
        policy_version=version,
    )


def _artifact(*, artifact_id: str, artifact_type: ArtifactType,
              created: datetime | None = None) -> ArtifactSpec:
    now = created or datetime.now(UTC)
    return ArtifactSpec(
        artifact_id=artifact_id,
        conversation_id="conv_1008",
        owner_user_id="u_east_user",
        type=artifact_type,
        permission_policy_version="policy_v18",
        catalog_version="catalog_v1",
        created_at=now,
        expires_at=now + timedelta(days=30),
        payload_ref=f"payload_{artifact_id}",
        source_result_ids=["result_abc"] if artifact_type != ArtifactType.FIELD_LIST else [],
        source_ref="obj_orders",
    )


def test_reference_resolver_binds_first_field_to_field_list_item():
    spec = _artifact(artifact_id="schema_list_023", artifact_type=ArtifactType.FIELD_LIST)
    resolved = ReferenceResolver().resolve(
        "用第一个字段查重复值",
        artifacts=[spec],
        payloads={spec.artifact_id: {
            "items": [
                {"ordinal": 1, "field": "orders.order_id"},
                {"ordinal": 2, "field": "orders.user_id"},
            ]
        }},
    )
    assert resolved.clarify is None
    assert resolved.artifact_id == "schema_list_023"
    assert resolved.field == "orders.order_id"


def test_reference_resolver_binds_just_now_to_latest_result_id():
    older = _artifact(artifact_id="table_1", artifact_type=ArtifactType.RESULT_TABLE)
    newer = _artifact(artifact_id="table_2", artifact_type=ArtifactType.RESULT_TABLE)
    resolved = ReferenceResolver().resolve(
        "把刚才结果做成表格",
        artifacts=[older, newer],
        payloads={
            older.artifact_id: {"result_id": "result_old"},
            newer.artifact_id: {"result_id": "result_new"},
        },
    )
    assert resolved.clarify is None
    assert resolved.artifact_id == "table_2"
    assert resolved.result_id == "result_new"


def test_reference_resolver_clarifies_when_no_artifact_exists():
    resolved = ReferenceResolver().resolve("用第一个字段查重复值", artifacts=[], payloads={})
    assert resolved.field is None
    assert resolved.artifact_id is None
    assert resolved.clarify


def test_prompt_context_builder_excludes_result_rows_and_secrets():
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        rolling_summary=None,
        latest_observation=ResultObservation(
            status=ResultStatus.SUCCESS,
            result_id="result_abc",
            summary=ResultSummary(
                row_count=2,
                columns=["gmv"],
                preview=[{"gmv": 99, "secret_row": "should-not-leak"}],
            ),
            query_plan_id="plan_1",
            catalog_version="catalog_v1",
            permission_policy_version="policy_v18",
        ),
        messages=[{"role": "user", "content": "昨天 GMV"}],
    )
    payload = PromptContextBuilder().build(node="agent_node", state=state)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "result_abc" in dumped
    assert "should-not-leak" not in dumped
    assert "secret_row" not in dumped
    assert "preview" not in dumped


def test_rolling_summary_tags_sources_and_keeps_full_mutation_spec():
    mutation = MutationSpec(
        operation="UPDATE",
        table="products",
        filters={"product_id": 1001},
        changes={"product_name": "新商品名称"},
        user_reason="修正商品名称",
        request_id="req_mut",
        user_id="u_admin",
        permission_policy_version="policy_v18",
        data_version="products_v18",
        idempotency_key="mut-1",
    )
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        task_frame=TaskFrame(
            task_id="task_1", user_id="u_east_user",
            question="昨天各品类 GMV", intent=Intent.DATA_QUERY,
            explicit_conditions=["只看华东"],
        ),
        result_ids=["result_abc"],
        artifact_ids=["artifact_1"],
        messages=[
            {"role": "user", "content": "昨天各品类 GMV"},
            {"role": "assistant", "content": "查询完成"},
        ],
        latest_observation=ResultObservation(
            status=ResultStatus.SUCCESS, result_id="result_abc",
            summary=ResultSummary(row_count=3, columns=["category_name", "gmv"]),
            query_plan_id="plan_1", catalog_version="catalog_v1",
            permission_policy_version="policy_v18",
        ),
    )
    summary = RollingSummaryBuilder().update(state, pending_mutation=mutation)
    sources = {fact.source for fact in summary.facts}
    assert sources == {"USER_CONFIRMED", "SYSTEM_OBSERVED"} or "USER_CONFIRMED" in sources
    assert "MODEL_INFERRED" not in sources or all(
        fact.source != "USER_CONFIRMED" for fact in summary.facts
        if "推测" in fact.text
    )
    assert summary.pending_mutation is not None
    assert summary.pending_mutation["filters"] == {"product_id": 1001}
    assert summary.pending_mutation["changes"]["product_name"] == "新商品名称"
    dumped = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False)
    assert "result_abc" in dumped
    assert "preview" not in dumped


def test_explicit_conditions_override_default_shop_preference():
    permission = _permission()
    base = TaskFrame(
        task_id="task_1", user_id="u_east_user",
        question="昨天 GMV", intent=Intent.DATA_QUERY,
    )
    with_default = apply_preferences(
        base, {"default_shop_id": "shop_001"}, permission)
    assert any(item.field.endswith("shop_id") and item.value == "shop_001"
               for item in with_default.filters)

    one_off = base.model_copy(update={"explicit_conditions": ["这次只看华东"]})
    with_one_off = apply_preferences(
        one_off, {"default_shop_id": "shop_001"}, permission)
    assert not any(item.value == "shop_001" for item in with_one_off.filters)


def test_one_off_filter_is_not_a_long_term_preference_write():
    assert is_long_term_preference_request("以后默认看店铺 A") is True
    assert is_long_term_preference_request("这次只看华东") is False
    assert is_long_term_preference_request("只看华东的 GMV") is False


def test_unauthorized_default_shop_is_not_applied():
    permission = _permission()
    task = TaskFrame(
        task_id="task_1", user_id="u_east_user",
        question="昨天 GMV", intent=Intent.DATA_QUERY,
    )
    framed = apply_preferences(task, {"default_shop_id": "shop_999"}, permission)
    assert framed.filters == []


def test_csv_and_chart_artifacts_reference_result_id(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'artifacts.db'}",
                               create_schema=True)
    runtime = build_test_runtime()
    runtime.persistence = store
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        task_frame=TaskFrame(
            task_id="task_1", user_id="u_east_user",
            question="做成表格和图表", intent=Intent.DATA_QUERY,
            deliverables=["DATA_TABLE", "CSV", "CHART", "TEXT"],
        ),
        grounded_context=GroundedContext(
            context_id="ctx_1", catalog_version="catalog_v1",
            coverage=CoverageStatus.SUFFICIENT, token_count=0,
            permission_policy_version="policy_v18",
        ),
        latest_observation=ResultObservation(
            status=ResultStatus.SUCCESS, result_id="result_abc",
            summary=ResultSummary(
                row_count=2, columns=["category_name", "gmv"]),
            query_plan_id="plan_1", catalog_version="catalog_v1",
            permission_policy_version="policy_v18",
        ),
        goal_checklist={"query_executed": True},
        coverage=CoverageStatus.SUFFICIENT,
    )
    asyncio.run(response_node(
        runtime, {"state": state, "permission": _permission()}))
    payloads = [
        store.get_artifact(artifact_id, user_id="u_east_user",
                           permission=_permission(), catalog_version="catalog_v1")
        for artifact_id in state.artifact_ids
    ]
    assert any(item.get("result_id") == "result_abc" and "download_path" in item
               for item in payloads)
    chart = next(item for item in payloads if item.get("type") in {"bar", "line", "horizontal_bar"})
    assert chart["result_id"] == "result_abc"
    assert chart["category_field"] == "category_name"
    assert chart["value_field"] == "gmv"


def test_field_list_artifact_uses_items_payload(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'fields.db'}",
                               create_schema=True)
    runtime = build_test_runtime()
    runtime.persistence = store
    from backend.app.models import CatalogField, CatalogObject
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        task_frame=TaskFrame(
            task_id="task_1", user_id="u_east_user",
            question="orders 表有哪些字段？", intent=Intent.SCHEMA_LOOKUP,
        ),
        grounded_context=GroundedContext(
            context_id="ctx_1", catalog_version="catalog_v1",
            coverage=CoverageStatus.SUFFICIENT, token_count=0,
            permission_policy_version="policy_v18",
            objects=[CatalogObject(
                object_id="obj_orders", name="orders", grain="order",
                source_id="mysql_ecommerce", domain="ECOMMERCE_TRADE",
                score=1, permission_policy_version="policy_v18")],
            fields=[CatalogField(
                field_id="fld_order_id", name="orders.order_id",
                data_type="bigint", object_id="obj_orders", score=1,
                permission_policy_version="policy_v18")],
        ),
        coverage=CoverageStatus.SUFFICIENT,
    )
    asyncio.run(response_node(
        runtime, {"state": state, "permission": _permission()}))
    payload = store.get_artifact(
        state.artifact_ids[0], user_id="u_east_user",
        permission=_permission(), catalog_version="catalog_v1")
    assert payload["items"][0] == {"ordinal": 1, "field": "orders.order_id"}
    assert "fields" not in payload


def test_understand_task_resolves_first_field_and_records_explicit_condition(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'followup.db'}",
                               create_schema=True)
    permission = _permission()
    spec = store.create_artifact(
        owner_user_id="u_east_user", conversation_id="t",
        artifact_type=ArtifactType.FIELD_LIST,
        payload={"items": [{"ordinal": 1, "field": "orders.order_id"}]},
        permission=permission, catalog_version="catalog_v1",
        source_ref="obj_orders",
    )
    runtime = build_test_runtime()
    runtime.persistence = store
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        artifact_ids=[spec.artifact_id],
        catalog_version="catalog_v1",
        previous_task_frame=TaskFrame(
            task_id="task_prev", user_id="u_east_user",
            question="orders 表有哪些字段？", intent=Intent.SCHEMA_LOOKUP,
        ),
    )
    frame = asyncio.run(understand_task(
        runtime, state, "用第一个字段查重复值，这次只看华东",
        "Asia/Shanghai", permission=permission,
        preferences={"default_shop_id": "shop_001"},
    ))
    assert "orders.order_id" in frame.mentions.get("fields", [])
    assert any("这次只看华东" in item or "华东" in item for item in frame.explicit_conditions)
    assert not any(item.value == "shop_001" for item in frame.filters)


def test_short_follow_up_inherits_metrics_and_schema_intent():
    from backend.app.graph._time_parser import parse_time_range

    runtime = build_test_runtime()
    yesterday = parse_time_range("昨天", "Asia/Shanghai")
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_demo_user",
        previous_task_frame=TaskFrame(
            task_id="task_prev", user_id="u_demo_user",
            question="昨天 GMV 是多少？", intent=Intent.DATA_QUERY,
            metric_ids=["gmv"], time_range=yesterday,
        ),
    )
    month = asyncio.run(understand_task(runtime, state, "上月呢", "Asia/Shanghai"))
    assert month.metric_ids == ["gmv"]
    assert month.intent == Intent.DATA_QUERY
    assert month.time_range is not None
    assert month.time_range.start < yesterday.start
    added = asyncio.run(understand_task(runtime, state, "再加上退款率", "Asia/Shanghai"))
    assert added.metric_ids == ["gmv", "refund_rate"]
    assert added.time_range == yesterday
    schema_state = AgentState(
        thread_id="t", request_id="r", user_id="u_demo_user",
        previous_task_frame=TaskFrame(
            task_id="task_prev", user_id="u_demo_user",
            question="orders 表有哪些字段？", intent=Intent.SCHEMA_LOOKUP,
        ),
    )
    field = asyncio.run(understand_task(
        runtime, schema_state, "支付时间字段是什么？", "Asia/Shanghai"))
    assert field.intent in {Intent.SCHEMA_LOOKUP, Intent.SCHEMA_QUERY}


def test_user_preference_overwrite_is_audited(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'prefs.db'}",
                               create_schema=True)
    first = store.put_user_preference(
        "u_east_user", "default_shop_id", "shop_001", confirmed=True)
    second = store.put_user_preference(
        "u_east_user", "default_shop_id", "shop_002", confirmed=True)
    history = store.user_memory_history("u_east_user", "default_shop_id")
    assert first["version"] == 1
    assert second["version"] == 2
    assert history[-1]["old_value"] == "shop_001"
    assert history[-1]["new_value"] == "shop_002"


def test_expired_interrupt_is_not_resumable():
    now = datetime.now(UTC)
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        status=RunStatus.WAITING_FOR_USER,
        pending_interrupt=Interrupt(
            reason="AMBIGUOUS_METRIC",
            question="退款率指哪一种？",
            checkpoint_id="ckpt_1",
            interrupt_id="interrupt_001",
            expires_at=now - timedelta(seconds=1),
        ),
    )

    class _Checkpoint:
        checkpoint_id = "ckpt_1"
        state_version = 1

    assert _interrupt_resumable(
        state, _Checkpoint(), user_id="u_east_user",
        interrupt_id="interrupt_001", now=now) is False


def test_duplicate_resume_returns_first_result(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'resume.db'}",
                               create_schema=True)
    first = store.put_idempotent(
        "resume-result:u_east_user:client-1", {"status": "SUCCEEDED", "answer": "已完成"})
    second = store.put_idempotent(
        "resume-result:u_east_user:client-1", {"status": "FAILED", "answer": "重放"})
    assert first == second
    assert second["status"] == "SUCCEEDED"


def test_checkpoint_conflict_on_stale_version(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'ckpt.db'}",
                               create_schema=True)
    state = AgentState(thread_id="t", request_id="r", user_id="u_east_user")
    first = store.save_checkpoint(state, expected_state_version=-1, idempotency_key="n:1")
    store.save_checkpoint(state, expected_state_version=first.state_version, idempotency_key="n:2")
    with pytest.raises(RuntimeAgentError, match="state version"):
        store.save_checkpoint(state, expected_state_version=0, idempotency_key="n:stale")


def test_stale_artifact_is_not_returned(tmp_path):
    store = RuntimePersistence(url=f"sqlite:///{tmp_path / 'stale.db'}",
                               create_schema=True)
    spec = store.create_artifact(
        owner_user_id="u_east_user", conversation_id="t",
        artifact_type=ArtifactType.RESULT_TABLE,
        payload={"result_id": "result_abc"},
        permission=_permission(), catalog_version="catalog_v1",
        source_result_ids=["result_abc"],
    )
    with pytest.raises(RuntimeAgentError, match="expired or no longer authorized"):
        store.get_artifact(
            spec.artifact_id, user_id="u_east_user",
            permission=_permission(version="policy_v19"),
            catalog_version="catalog_v1")


def test_query_generation_prompt_excludes_chat_history_and_rows():
    state = AgentState(
        thread_id="t", request_id="r", user_id="u_east_user",
        task_frame=TaskFrame(
            task_id="task_1", user_id="u_east_user",
            question="昨天 GMV", intent=Intent.DATA_QUERY,
        ),
        grounded_context=GroundedContext(
            context_id="ctx_1", catalog_version="catalog_v1",
            coverage=CoverageStatus.SUFFICIENT, token_count=0,
            permission_policy_version="policy_v18",
        ),
        messages=[{"role": "user", "content": "闲聊内容不应进入 SQL 生成"}],
        latest_observation=ResultObservation(
            status=ResultStatus.SUCCESS, result_id="result_abc",
            summary=ResultSummary(
                row_count=1, columns=["gmv"],
                preview=[{"gmv": 1, "secret_row": "nope"}]),
            query_plan_id="plan_1", catalog_version="catalog_v1",
            permission_policy_version="policy_v18",
        ),
    )
    payload = PromptContextBuilder().build(node="query_generation_node", state=state)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "catalog_v1" in dumped
    assert "闲聊内容不应进入 SQL 生成" not in dumped
    assert "secret_row" not in dumped
