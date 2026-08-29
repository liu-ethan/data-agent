from __future__ import annotations

from backend.app.catalog.models import CatalogSnapshot, MetricSpec
from backend.app.types import QuerySkillResult, QueryTask, RuntimeContext, SkillErrorCode
from backend.app.resources.domain import tenant_id


def check_query_coverage(
    task: QueryTask,
    ctx: RuntimeContext,
    catalog: CatalogSnapshot,
) -> tuple[list[MetricSpec], QuerySkillResult | None]:
    if ctx.permissions.tenant_id != tenant_id() or ctx.tenant_id != tenant_id():
        return [], QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.REJECTED,
            error_message=f"tenant_id must be {tenant_id()}",
        )
    if not task.metric_ids:
        return [], QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.REJECTED,
            error_message="metric_ids required",
        )
    by_id = {metric.metric_id: metric for metric in catalog.metrics}
    allowed = set(ctx.permissions.allowed_metrics)
    missing = [mid for mid in task.metric_ids if mid not in by_id]
    denied = [mid for mid in task.metric_ids if mid not in allowed]
    if missing or denied:
        bad = missing or denied
        return [], QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.REJECTED,
            error_message=f"metric not allowed or unknown: {', '.join(bad)}",
        )
    return [by_id[mid] for mid in task.metric_ids], None
