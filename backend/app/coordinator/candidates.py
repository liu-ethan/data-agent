from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import Engine

from backend.app.catalog.models import CatalogSnapshot
from backend.app.resources.domain import sku_search_limit
from backend.app.resources.prompts import render_prompt
from backend.app.resources.sql import mysql_text
from backend.app.types import PermissionSet

Candidate = dict[str, str]
ProductSearch = Callable[..., list[dict[str, Any]]]
TimeSearch = Callable[..., str | None]

_FIELD = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


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
    stmt = mysql_text("write.search_sku", limit=str(sku_search_limit()))
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt, {"q": f"%{query}%"}).mappings()]


def enrich_hitl(
    payload: dict[str, Any],
    *,
    catalog: CatalogSnapshot,
    permissions: PermissionSet,
    llm: Any | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    payload = dict(payload)
    existing = [
        dict(item)
        for item in (payload.get("candidates") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if existing:
        pool = existing
    elif payload.get("kind") == "query_error" or payload.get("clarify_kind") == "metric":
        pool = lookup_metrics("", catalog, permissions)
    else:
        pool = []
    payload["candidates"] = pool
    payload["message"] = payload.get("message") or _fallback_message(payload, catalog)
    phrase = getattr(llm, "phrase_clarify", None) if llm is not None and pool else None
    if phrase is None:
        return payload
    body = {
        "user_message": user_message,
        "kind": payload.get("kind"),
        "clarify_kind": payload.get("clarify_kind"),
        "error_code": payload.get("error_code"),
        "missing_concept": (payload.get("schema_gap") or {}).get("missing_concept"),
        "purpose": (payload.get("schema_gap") or {}).get("purpose"),
        "pool": pool,
    }
    try:
        raw = phrase(body, render_prompt("coordinator.clarify"))
    except Exception:  # noqa: BLE001
        return payload
    return _apply_phrasing(payload, raw, {item["id"] for item in pool})


def _fallback_message(payload: dict[str, Any], catalog: CatalogSnapshot) -> str:
    kind = payload.get("clarify_kind")
    if kind == "metric":
        return "想看哪个指标？"
    if kind == "product":
        return "要操作哪件商品？"
    if kind == "time":
        return "看哪一段时间？"
    if payload.get("error_code") == "SCHEMA_GAP":
        hint = _friendly_concept((payload.get("schema_gap") or {}).get("missing_concept"), catalog)
        if hint:
            return f"按{hint}查时还缺条件，选一个继续："
        return "这个问法还缺条件，选一个继续："
    if payload.get("error_code") == "AMBIGUOUS":
        return "有几种理解，选一个："
    if payload.get("error_code") == "UNSAFE_SQL":
        return "这个问法没法安全查到数据，选一个继续："
    if payload.get("status") == "not_found":
        return "未查到可选项，请换一种问法。"
    return str(payload.get("message") or "请选择一项：")


def _friendly_concept(concept: object, catalog: CatalogSnapshot) -> str:
    text = str(concept or "").strip()
    if not text or _FIELD.match(text):
        table, _, column = text.partition(".")
        for item in catalog.tables:
            if item.table_name != table:
                continue
            for col in catalog.columns:
                if col.table_name == table and col.column_name == column:
                    return col.comment or item.business_name
            return item.business_name
        return ""
    if _FIELD.search(text):
        return ""
    return text


def _apply_phrasing(
    payload: dict[str, Any],
    raw: object,
    allowed: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return payload
    message = str(raw.get("message") or "").strip()
    if message and not _FIELD.search(message):
        payload["message"] = message
    by_id = {item["id"]: dict(item) for item in payload.get("candidates") or []}
    picked: list[dict[str, str]] = []
    for item in raw.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "")
        if cid not in allowed:
            continue
        base = by_id[cid]
        label = str(item.get("label") or "").strip()
        if label and not _FIELD.search(label):
            base["label"] = label
        picked.append(base)
    if picked:
        payload["candidates"] = picked
    return payload
