"""Deterministic MutationSpec construction for governed Admin writes."""

from __future__ import annotations

import re
from uuid import uuid4

from ..models import MutationSpec, PermissionContext, TaskFrame

_PRODUCT_ID = re.compile(r"prod_\d+", re.IGNORECASE)
_NAME_AFTER = re.compile(
    r"(?:名称|名字|商品名).{0,8}(?:改成|改为)\s*(.+)$",
)
_CONFIRM = {"确认执行", "确认", "是", "yes", "approve", "批准"}
_CANCEL = {"取消", "拒绝", "cancel", "否"}
_FORBIDDEN = (
    "删除",
    "drop table",
    "truncate",
    "grant ",
    "开通写入",
    "load_file",
    "outfile",
    "delete from",
)


def is_write_confirmation(message: str) -> bool:
    return message.strip().lower() in {item.lower() for item in _CONFIRM}


def is_write_cancellation(message: str) -> bool:
    return message.strip().lower() in {item.lower() for item in _CANCEL}


def forbidden_mutation_message(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in _FORBIDDEN) or any(
        token in message for token in ("删除", "开通写入")
    )


def looks_like_mutation(message: str) -> bool:
    if "改成看" in message:
        return False
    if forbidden_mutation_message(message):
        return True
    if any(token in message for token in ("改个名字", "插入一条", "插入一", "预览后提交", "开通写入")):
        return True
    if any(token in message for token in ("把", "将", "给", "批量", "更新")) and any(
        token in message for token in ("改成", "改为", "修改", "更新", "改个")
    ) and any(
        token in message
        for token in ("商品", "产品", "prod_", "订单", "退款", "权限", "名称", "名字")
    ):
        return True
    return False


def build_mutation_spec(
    message: str,
    *,
    task: TaskFrame,
    permission: PermissionContext,
    request_id: str,
) -> MutationSpec | None:
    product_id = _product_id(message)
    new_name = _new_product_name(message)
    if product_id and new_name:
        return MutationSpec(
            operation="UPDATE",
            table="products",
            filters={"product_id": product_id},
            changes={"product_name": new_name.strip()},
            user_reason=task.question,
            request_id=request_id,
            user_id=permission.user_id,
            permission_policy_version=permission.policy_version,
            data_version="pending",
            idempotency_key=f"mut_{request_id}",
        )
    if "插入" in message:
        return MutationSpec(
            operation="INSERT",
            table="refunds",
            filters={},
            changes={"refund_id": f"refund_{uuid4().hex[:8]}"},
            user_reason=task.question,
            request_id=request_id,
            user_id=permission.user_id,
            permission_policy_version=permission.policy_version,
            data_version="pending",
            idempotency_key=f"mut_{request_id}",
        )
    if any(token in message for token in ("订单状态", "取消订单", "退款状态", "订单金额")):
        table = "refunds" if "退款" in message else "orders"
        field = "status"
        return MutationSpec(
            operation="UPDATE",
            table=table,
            filters={},
            changes={field: "PAID" if table == "orders" else "SUCCESS"},
            user_reason=task.question,
            request_id=request_id,
            user_id=permission.user_id,
            permission_policy_version=permission.policy_version,
            data_version="pending",
            idempotency_key=f"mut_{request_id}",
        )
    return None


def _product_id(message: str) -> str | None:
    match = _PRODUCT_ID.search(message)
    return match.group(0).lower() if match else None


def _new_product_name(message: str) -> str | None:
    match = _NAME_AFTER.search(message)
    if match:
        return match.group(1).strip()
    return None
