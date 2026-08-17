from backend.app.models import CoverageStatus, PermissionContext, TaskFrame
from backend.app.services.catalog_baseline import (
    CatalogRetrievalService,
    HybridCatalogRetrievalService,
    SyntheticCatalogRetrievalService,
    generate_synthetic_metadata,
)


def test_synthetic_metadata_has_spec_scale_and_bounded_context():
    records = generate_synthetic_metadata()
    assert len(records) == 1000
    assert sum(len(record.fields) for record in records) == 30_000
    service = SyntheticCatalogRetrievalService(max_objects=5, max_fields=8, max_tokens=3000)
    permission = PermissionContext(
        user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_001"], policy_version="p"
    )
    task = TaskFrame(
        task_id="t", user_id="u", question="synthetic_table_0042", intent="SCHEMA_LOOKUP"
    )
    context, coverage = service.retrieve(task, permission)
    assert context.objects and context.objects[0].name == "synthetic_table_0042"
    assert context.token_count <= 3000
    assert all(0 <= item.score <= 1 for item in context.objects)
    assert coverage.status.value == "SUFFICIENT"


def test_hybrid_retrieval_records_method_and_normalized_scores():
    service = HybridCatalogRetrievalService(max_objects=5, max_fields=8, max_tokens=3000)
    permission = PermissionContext(
        user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_001"], policy_version="p"
    )
    task = TaskFrame(
        task_id="t",
        user_id="u",
        question="昨天各品类 GMV",
        intent="DATA_QUERY",
        metric_ids=["category_gmv"],
    )
    context, _ = service.retrieve(task, permission)
    assert context.objects and all(
        item.retrieval_method == "bm25+embedding+reranker" for item in context.objects
    )
    assert all(0 <= item.score <= 1 for item in context.objects)


def test_baseline_retrieval_keeps_aov_and_shop_bindings():
    service = CatalogRetrievalService(max_objects=5, max_fields=8, max_tokens=3000)
    permission = PermissionContext(
        user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_001"], policy_version="p"
    )
    aov, coverage = service.retrieve(
        TaskFrame(
            task_id="t",
            user_id="u",
            question="昨天客单价是多少？",
            intent="DATA_QUERY",
            metric_ids=["average_order_value"],
        ),
        permission,
    )
    assert coverage.status == CoverageStatus.SUFFICIENT
    assert "average_order_value" in aov.metrics
    shops, _ = service.retrieve(
        TaskFrame(
            task_id="t",
            user_id="u",
            question="昨天各店铺 GMV 是多少？",
            intent="DATA_QUERY",
            metric_ids=["gmv"],
            dimension_ids=["shops.shop_name"],
        ),
        permission,
    )
    assert any(item.name == "shops" for item in shops.objects)
    assert any(item.name == "shops.shop_name" for item in shops.fields)
