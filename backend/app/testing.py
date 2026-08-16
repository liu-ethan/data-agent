"""Explicit local test-double composition.

Production modules never import this file. It is the sole place that combines
the deterministic catalog, SQLite data adapter and in-memory result store.
"""

from __future__ import annotations

from typing import Any

from .gateways import ReadGateway
from .graph import RuntimeGraph
from .models import PermissionContext
from .services.catalog_baseline import CatalogRetrievalService, build_permission
from .testing_adapters import ResultRepository, SQLiteDataRepository


def build_test_gateway(*, settings: dict[str, Any] | None = None) -> ReadGateway:
    return ReadGateway(
        data=SQLiteDataRepository(),
        results=ResultRepository(),
        settings=settings,
    )


def build_test_runtime(*, settings: dict[str, Any] | None = None,
                       llm: Any | None = None) -> RuntimeGraph:
    values = settings or {}
    budget = values.get("retrieval_budget", {})
    retrieval = CatalogRetrievalService(
        max_objects=int(budget.get("max_object_candidates", 5)),
        max_fields=int(budget.get("max_fields_per_object", 8)),
        max_tokens=int(budget.get("max_context_tokens", 3000)),
        min_score=float(budget.get("min_rerank_score", 0.55)),
        ambiguity_gap=float(budget.get("ambiguity_score_gap", 0.08)),
    )
    return RuntimeGraph(
        retrieval=retrieval,
        gateway=build_test_gateway(settings=values.get("read_query", {})),
        settings=values,
        llm=llm,
    )


def build_test_permission(user_id: str,
                          settings: dict[str, Any] | None = None) -> PermissionContext:
    values = settings or {
        "default_policy_version": "policy_test_v1",
        "denied_classifications": ["PHONE", "ID_CARD"],
        "demo_scopes": {
            "u_demo_user": {"role": "USER", "shop_ids": ["shop_001", "shop_002"]},
            "u_demo_admin": {"role": "ADMIN", "shop_ids": ["shop_001", "shop_002", "shop_003"]},
        },
    }
    return build_permission(user_id, values)
