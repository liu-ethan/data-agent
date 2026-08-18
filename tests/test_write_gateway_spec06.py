"""Spec 06 acceptance tests for WriteGateway and approval HITL.

Covers:
- User writes rejected before HITL
- Admin forbidden operations rejected without approval
- Whitelist table/field enforcement
- Preview before/after/affected-row contract
- Stale permission or data version invalidates an approved preview
- Checkpoint replay does not double-submit
- Audit can reconstruct operator, request, diff, approval and result
"""

from __future__ import annotations

import pytest

from backend.app.config import load_settings
from backend.app.errors import RuntimeAgentError
from backend.app.gateways.write_gateway import WriteGateway
from backend.app.models import MutationSpec, PermissionContext, ResultStatus, RunStatus
from backend.app.repositories.runtime import RuntimePersistence
from backend.app.testing import build_test_permission, build_test_runtime
from backend.app.testing_adapters import SQLiteDataRepository


def _admin() -> PermissionContext:
    return build_test_permission("u_demo_admin")


def _user() -> PermissionContext:
    return build_test_permission("u_demo_user")


def _spec(**overrides: object) -> MutationSpec:
    payload = {
        "operation": "UPDATE",
        "table": "products",
        "filters": {"product_id": "prod_1001"},
        "changes": {"product_name": "新智能手机"},
        "user_reason": "修正商品名称",
        "request_id": "req_mut",
        "user_id": "u_demo_admin",
        "permission_policy_version": "policy_test_v1",
        "data_version": "pending",
        "idempotency_key": "mut-1",
    }
    payload.update(overrides)
    return MutationSpec.model_validate(payload)


def _product(store: SQLiteDataRepository, product_id: str = "prod_1001") -> dict:
    rows = store.fetch("SELECT * FROM products WHERE product_id = :id", {"id": product_id})
    return rows[0]


def _gateway(store: SQLiteDataRepository | None = None, *, auditor=None) -> tuple[WriteGateway, SQLiteDataRepository]:
    data = store or SQLiteDataRepository()
    return WriteGateway(data=data, auditor=auditor), data


def test_user_write_is_rejected_before_preview():
    gateway, _ = _gateway()
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(user_id="u_demo_user"), _user())
    assert exc.value.error_code == "WRITE_FORBIDDEN"


def test_admin_forbidden_operations_never_enter_preview():
    gateway, _ = _gateway()
    admin = _admin()
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(
            _spec(operation="INSERT", table="refunds", filters={}, changes={"refund_id": "refund_x"}),
            admin,
        )
    assert exc.value.error_code == "WRITE_FORBIDDEN"
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(table="orders", filters={"order_id": "ord_001"}, changes={"status": "PAID"}), admin)
    assert exc.value.error_code == "WRITE_FORBIDDEN"


def test_whitelist_rejects_non_product_name_fields():
    gateway, _ = _gateway()
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(changes={"status": "INACTIVE"}), _admin())
    assert exc.value.error_code == "WRITE_FORBIDDEN"


def test_update_without_unique_key_is_rejected():
    gateway, _ = _gateway()
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(filters={}), _admin())
    assert exc.value.error_code == "WRITE_FORBIDDEN"
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(filters={"shop_id": "shop_001", "product_id": "prod_1001"}), _admin())
    assert exc.value.error_code == "WRITE_FORBIDDEN"


def test_preview_includes_before_after_rows_and_full_spec():
    gateway, _ = _gateway()
    preview = gateway.preview(_spec(), _admin())
    assert preview.operation == "UPDATE"
    assert preview.target == "products.product_id=prod_1001"
    assert preview.estimated_affected_rows == 1
    assert preview.diff["product_name"]["before"] == "智能手机"
    assert preview.diff["product_name"]["after"] == "新智能手机"
    assert preview.mutation_spec.filters == {"product_id": "prod_1001"}
    assert preview.mutation_spec.changes == {"product_name": "新智能手机"}
    assert preview.data_version != "pending"
    assert preview.schema_version == "mutation_preview_v1"


def test_stale_data_version_invalidates_approved_preview():
    gateway, store = _gateway()
    preview = gateway.preview(_spec(), _admin())
    store.apply_update("products", {"product_id": "prod_1001"}, {"product_name": "已被别人改过"})
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.commit(preview, _admin())
    assert exc.value.error_code == "MUTATION_STALE"
    assert _product(store)["product_name"] == "已被别人改过"


def test_stale_permission_invalidates_approved_preview():
    gateway, store = _gateway()
    preview = gateway.preview(_spec(), _admin())
    stale = preview.mutation_spec.model_copy(update={"permission_policy_version": "policy_old"})
    stale_preview = preview.model_copy(update={"mutation_spec": stale, "permission_policy_version": "policy_old"})
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.commit(stale_preview, _admin())
    assert exc.value.error_code == "MUTATION_STALE"
    assert _product(store)["product_name"] == "智能手机"


def test_replay_of_committed_mutation_does_not_double_submit():
    auditor = RuntimePersistence(url="sqlite://", create_schema=True)
    gateway, store = _gateway(auditor=auditor)
    preview = gateway.preview(_spec(), _admin())
    first = gateway.commit(preview, _admin())
    second = gateway.commit(preview, _admin())
    assert first.status == ResultStatus.SUCCESS
    assert second.status == ResultStatus.SUCCESS
    assert first.affected_rows == 1
    assert second.affected_rows == 0
    assert first.audit_id == second.audit_id
    assert _product(store)["product_name"] == "新智能手机"


def test_unavailable_audit_rejects_preview_before_mutating():
    class _UnavailableAuditor:
        def ensure_mutation_audit(self) -> None:
            raise RuntimeAgentError(
                "MUTATION_EXECUTION_FAILED",
                "mutation audit table is not available",
            )

        def record_mutation_audit(self, **kwargs):
            raise AssertionError("audit must not run when the table is unavailable")

    gateway, store = _gateway(auditor=_UnavailableAuditor())
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.preview(_spec(), _admin())
    assert exc.value.error_code == "MUTATION_EXECUTION_FAILED"
    assert _product(store)["product_name"] == "智能手机"


def test_audit_insert_failure_is_not_an_unexplained_internal_error():
    class _BrokenAuditor:
        def ensure_mutation_audit(self) -> None:
            return None

        def record_mutation_audit(self, **kwargs):
            raise RuntimeError("mutation_audit insert failed")

    gateway, store = _gateway(auditor=_BrokenAuditor())
    preview = gateway.preview(_spec(), _admin())
    with pytest.raises(RuntimeAgentError) as exc:
        gateway.commit(preview, _admin())
    assert exc.value.error_code == "MUTATION_EXECUTION_FAILED"


def test_audit_records_operator_request_diff_approval_and_result():
    auditor = RuntimePersistence(url="sqlite://", create_schema=True)
    gateway, _ = _gateway(auditor=auditor)
    preview = gateway.preview(_spec(), _admin())
    observation = gateway.commit(preview, _admin())
    records = auditor.mutation_audits(idempotency_key="mut-1")
    assert len(records) == 1
    audit = records[0]
    assert audit["user_id"] == "u_demo_admin"
    assert audit["request_id"] == "req_mut"
    assert audit["table_name"] == "products"
    assert audit["before_values"]["product_name"] == "智能手机"
    assert audit["after_values"]["product_name"] == "新智能手机"
    assert audit["decision"] == "APPROVED"
    assert audit["status"] == "SUCCESS"
    assert audit["affected_rows"] == 1
    assert audit["audit_id"] == observation.audit_id


def test_user_natural_language_write_is_rejected_without_interrupt():
    graph = build_test_runtime(settings=load_settings().raw)
    response = graph.run(
        message="把商品 prod_1001 的名称改成 新智能手机",
        user_id="u_demo_user",
        permission=_user(),
    )
    assert response.status == RunStatus.REJECTED
    assert response.interrupt is None
    failed = [event for event in response.events if event.get("event") == "run.failed"]
    assert failed and failed[-1].get("error_code") == "WRITE_FORBIDDEN"


def test_admin_delete_is_rejected_without_interrupt():
    graph = build_test_runtime(settings=load_settings().raw)
    response = graph.run(
        message="删除测试订单 ord_001",
        user_id="u_demo_admin",
        permission=_admin(),
    )
    assert response.status == RunStatus.REJECTED
    assert response.interrupt is None
    failed = [event for event in response.events if event.get("event") == "run.failed"]
    assert failed and failed[-1].get("error_code") == "WRITE_FORBIDDEN"


def test_admin_product_rename_preview_then_hitl_resume(tmp_path):
    database = tmp_path / "write-hitl.db"
    store = SQLiteDataRepository()
    persistence = RuntimePersistence(url=f"sqlite:///{database}", create_schema=True)
    graph = build_test_runtime(settings=load_settings().raw, data=store)
    graph.persistence = persistence
    waiting = graph.run(
        message="把商品 prod_1001 的名称改成 新智能手机",
        user_id="u_demo_admin",
        permission=_admin(),
    )
    assert waiting.status == RunStatus.WAITING_FOR_USER
    assert waiting.interrupt is not None
    assert waiting.interrupt.reason == "WRITE_APPROVAL"
    assert waiting.interrupt.preview is not None
    assert waiting.interrupt.preview.diff["product_name"]["before"] == "智能手机"
    assert waiting.interrupt.preview.diff["product_name"]["after"] == "新智能手机"
    assert waiting.interrupt.preview.estimated_affected_rows == 1
    assert _product(store)["product_name"] == "智能手机"

    committed = graph.run(
        message="确认执行",
        user_id="u_demo_admin",
        permission=_admin(),
        thread_id=waiting.thread_id,
        resume=True,
        expected_state_version=waiting.state_version,
    )
    assert committed.status == RunStatus.SUCCEEDED
    assert _product(store)["product_name"] == "新智能手机"


def test_admin_cancels_write_without_mutating_data(tmp_path):
    database = tmp_path / "write-cancel.db"
    store = SQLiteDataRepository()
    persistence = RuntimePersistence(url=f"sqlite:///{database}", create_schema=True)
    graph = build_test_runtime(settings=load_settings().raw, data=store)
    graph.persistence = persistence
    waiting = graph.run(
        message="把商品 prod_1001 的名称改成 新智能手机",
        user_id="u_demo_admin",
        permission=_admin(),
    )
    cancelled = graph.run(
        message="取消",
        user_id="u_demo_admin",
        permission=_admin(),
        thread_id=waiting.thread_id,
        resume=True,
        expected_state_version=waiting.state_version,
    )
    assert cancelled.status == RunStatus.SUCCEEDED
    assert cancelled.answer == "已取消本次写入，数据未修改。"
    assert _product(store)["product_name"] == "智能手机"
