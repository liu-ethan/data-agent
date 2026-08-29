from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.app.catalog.models import CatalogSnapshot
from backend.app.types import PermissionSet

Candidate = dict[str, str]
ProductSearch = Callable[..., list[dict[str, Any]]]
TimeSearch = Callable[..., str | None]


def lookup_metrics(
    query: str,
    catalog: CatalogSnapshot,
    permissions: PermissionSet,
) -> list[Candidate]:
    needle = (query or "").strip().lower()
    allowed = set(permissions.allowed_metrics)
    hits: list[Candidate] = []
    for metric in catalog.metrics:
        if metric.metric_id not in allowed:
            continue
        mid = metric.metric_id.lower()
        name = (metric.name or "").lower()
        blob = f"{mid} {name}"
        if not needle or needle in blob or mid in needle or (name and name in needle):
            hits.append({"id": metric.metric_id, "label": metric.name, "kind": "metric"})
    return hits


def lookup_products(
    query: str,
    permissions: PermissionSet,
    *,
    search_fn: ProductSearch | None = None,
    engine: Engine | None = None,
) -> list[Candidate]:
    if "dim_sku" not in permissions.allowed_tables:
        return []
    rows: list[dict[str, Any]] = []
    if search_fn is not None:
        rows = list(search_fn(query, permissions) or [])
    elif engine is not None:
        rows = _search_sku(engine, query)
    out: list[Candidate] = []
    for row in rows:
        sku_id = row.get("id")
        if sku_id is None:
            continue
        label = row.get("sku_name") or row.get("label") or ""
        out.append({"id": str(sku_id), "label": str(label), "kind": "product"})
    return out


def lookup_time_range(
    permissions: PermissionSet,
    *,
    max_time_fn: TimeSearch | None = None,
) -> list[Candidate]:
    if max_time_fn is None:
        return []
    value = max_time_fn(permissions)
    if not value:
        return []
    return [{"id": "data_range", "label": str(value), "kind": "time"}]


def _search_sku(engine: Engine, query: str) -> list[dict[str, Any]]:
    stmt = text(
        "SELECT id, sku_name FROM dim_sku WHERE sku_name LIKE :q LIMIT 20"
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt, {"q": f"%{query}%"}).mappings()]
