"""Resolve '刚才' / '第一个字段' against prior Artifact payloads."""

from __future__ import annotations

import re
from typing import Any

from ..models import ArtifactSpec, ArtifactType, Contract


class ResolvedReference(Contract):
    artifact_id: str | None = None
    field: str | None = None
    result_id: str | None = None
    clarify: str | None = None


def _items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("items") or payload.get("fields") or []
    return [row for row in rows if isinstance(row, dict)]


_CN_ORDINAL = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _ordinal(text: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*(?:个|项|列|字段)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"第\s*([一二两三四五六七八九十])\s*(?:个|项|列|字段)", text)
    if match:
        return _CN_ORDINAL[match.group(1)]
    return None


class ReferenceResolver:
    def resolve(
        self,
        text: str,
        *,
        artifacts: list[ArtifactSpec],
        payloads: dict[str, Any],
    ) -> ResolvedReference:
        if not artifacts:
            return ResolvedReference(clarify="no prior artifact is available")
        ordinal = _ordinal(text)
        field_lists = [item for item in artifacts if item.type == ArtifactType.FIELD_LIST]
        if ordinal is not None:
            if not field_lists:
                return ResolvedReference(clarify="no field list artifact is available")
            spec = field_lists[-1]
            items = _items(payloads.get(spec.artifact_id))
            for row in items:
                if int(row.get("ordinal") or 0) == ordinal and row.get("field"):
                    return ResolvedReference(
                        artifact_id=spec.artifact_id, field=str(row["field"]))
            return ResolvedReference(clarify="the requested field ordinal is not in the artifact")
        if any(term in text for term in ("刚才", "上一个", "上一张", "结果")):
            tables = [item for item in artifacts
                      if item.type in {ArtifactType.RESULT_TABLE, ArtifactType.CSV,
                                       ArtifactType.CHART_DSL}]
            spec = (tables or artifacts)[-1]
            payload = payloads.get(spec.artifact_id) or {}
            result_id = payload.get("result_id") if isinstance(payload, dict) else None
            if not result_id and spec.source_result_ids:
                result_id = spec.source_result_ids[-1]
            return ResolvedReference(artifact_id=spec.artifact_id, result_id=result_id)
        return ResolvedReference(clarify="reference is ambiguous")
