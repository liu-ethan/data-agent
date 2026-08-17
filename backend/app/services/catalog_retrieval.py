"""Permission-first hierarchical Schema RAG orchestration."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..errors import RuntimeAgentError
from ..models import (
    CatalogField,
    CatalogObject,
    CoverageResult,
    GroundedContext,
    JoinPath,
    PermissionContext,
    SchemaGap,
    TaskFrame,
)
from ..repositories.catalog import MySQLCatalogRepository
from ..repositories.catalog_index import MilvusCatalogIndex
from .coverage import CoverageEvaluator
from .embedding import build_embedder


def _tokens(value: object) -> int:
    return max(
        1, (len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)) + 3) // 4
    )


def _fuse(
    dense: list[dict[str, Any]],
    lexical: list[dict[str, Any]],
    *,
    dense_weight: float = 0.6,
    lexical_weight: float = 0.4,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Weighted score fusion with stable provenance and normalized output."""
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for method, weight, hits in (
        ("milvus-dense", dense_weight, dense),
        ("mysql-bm25", lexical_weight, lexical),
    ):
        for hit in hits:
            key = (str(hit["kind"]), str(hit["target_id"]))
            item = entries.setdefault(key, dict(hit) | {"score": 0.0, "methods": []})
            item["score"] += weight * float(hit["score"])
            item["methods"].append(method)
            if not item.get("object_id") and hit.get("object_id"):
                item["object_id"] = hit["object_id"]
    ranked = sorted(
        entries.values(),
        key=lambda item: (-float(item["score"]), str(item["kind"]), str(item["target_id"])),
    )[:limit]
    maximum = max((float(item["score"]) for item in ranked), default=1)
    return [
        item
        | {
            "score": round(float(item["score"]) / maximum, 6),
            "retrieval_method": "+".join(item["methods"]),
        }
        for item in ranked
    ]


def _merge_by_id(prior: list, incoming: list, *, key) -> list:
    merged = {key(item): item for item in prior}
    for item in incoming:
        merged[key(item)] = item
    return list(merged.values())


class ContextBudgeter:
    """Keep required evidence first and enforce a serialized context ceiling."""

    tokenizer_version = "cl100k_base_estimate_v1"

    def __init__(self, max_tokens: int, max_fields_per_object: int) -> None:
        self.max_tokens = max_tokens
        self.max_fields_per_object = max_fields_per_object

    def apply(
        self,
        *,
        objects: list[CatalogObject],
        fields: list[CatalogField],
        joins: list[JoinPath],
        metrics: list[str],
        required_fields: set[str],
    ) -> tuple[list[CatalogObject], list[CatalogField], list[JoinPath], int]:
        grouped: dict[str, list[CatalogField]] = defaultdict(list)
        for field in fields:
            grouped[field.object_id].append(field)
        selected_fields: list[CatalogField] = []
        for item in objects:
            candidates = grouped[item.object_id]
            candidates.sort(
                key=lambda field: (
                    0
                    if field.name in required_fields
                    else 1
                    if field.classification == "BUSINESS_TIME"
                    else 2
                    if field.classification == "IDENTIFIER"
                    else 3,
                    -field.score,
                    field.name,
                )
            )
            required = [field for field in candidates if field.name in required_fields]
            optional = [field for field in candidates if field.name not in required_fields]
            selected_fields.extend(
                required + optional[: max(0, self.max_fields_per_object - len(required))]
            )

        def payload() -> dict[str, Any]:
            return {
                "objects": [item.model_dump() for item in objects],
                "fields": [item.model_dump() for item in selected_fields],
                "metrics": metrics,
                "joins": [item.model_dump() for item in joins],
            }

        # Aliases are optional evidence decoration and are trimmed first.
        if _tokens(payload()) > self.max_tokens:
            selected_fields = [
                field.model_copy(update={"aliases": []}) for field in selected_fields
            ]
        optional_indexes = [
            index
            for index, field in enumerate(selected_fields)
            if field.name not in required_fields
        ]
        while _tokens(payload()) > self.max_tokens and optional_indexes:
            selected_fields.pop(optional_indexes.pop())
            optional_indexes = [
                index
                for index, field in enumerate(selected_fields)
                if field.name not in required_fields
            ]
        while _tokens(payload()) > self.max_tokens and len(joins) > 1:
            joins = joins[:-1]
        if _tokens(payload()) > self.max_tokens:
            raise RuntimeAgentError(
                "RAG_CONTEXT_BUDGET_EXCEEDED",
                "required grounded evidence exceeds the configured token budget",
                details={"max_context_tokens": self.max_tokens},
            )
        return objects, selected_fields, joins, _tokens(payload())


class _RerankResult(BaseModel):
    model_config = {"extra": "forbid"}
    ranking: list[str]
    schema_version: str = "rerank_v1"


class PassthroughReranker:
    """Ablation reranker: keep the permission-filtered order unchanged."""

    async def rerank(
        self, query: str, objects: list[CatalogObject]
    ) -> tuple[list[str], dict[str, Any]]:
        return [item.object_id for item in objects], {
            "purpose": "reranker",
            "disabled_for_ablation": True,
        }


class LLMReranker:
    """Rerank only candidates already filtered by authoritative permission."""

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    async def rerank(
        self, query: str, objects: list[CatalogObject]
    ) -> tuple[list[str], dict[str, Any]]:
        if not objects:
            return [], {"purpose": "reranker", "skipped": "no_candidates"}
        draft, trace = await self.llm.structured(
            system=(
                "Rank catalog object IDs by relevance to the user question. "
                "Return every candidate ID exactly once, most relevant first. "
                "Do not add IDs."
            ),
            user=json.dumps(
                {
                    "question": query,
                    "candidates": [
                        {
                            "object_id": item.object_id,
                            "name": item.name,
                            "grain": item.grain,
                            "domain": item.domain,
                        }
                        for item in objects
                    ],
                },
                ensure_ascii=False,
            ),
            schema=_RerankResult,
            purpose="reranker",
            temperature=0.0,
            prompt_version="catalog_rerank_v1",
        )
        expected = {item.object_id for item in objects}
        returned = [item for item in dict.fromkeys(draft.ranking) if item in expected]
        dropped = len(set(draft.ranking) - expected)
        missing = [item.object_id for item in objects if item.object_id not in returned]
        ranking = [*returned, *missing]
        return ranking, asdict(trace) | {
            "purpose": "reranker",
            "completed_missing_ids": len(missing),
            "dropped_unknown_ids": dropped,
        }


class ProductionCatalogRetrievalService:
    """Hierarchical BM25+dense retrieval with deterministic coverage checks."""

    def __init__(
        self,
        repository: MySQLCatalogRepository,
        index: MilvusCatalogIndex,
        embedder: Any,
        reranker: Any,
        *,
        max_sources: int = 3,
        max_objects: int = 5,
        max_fields: int = 8,
        max_join_hops: int = 2,
        max_tokens: int = 3000,
        min_score: float = 0.55,
        ambiguity_gap: float = 0.08,
        dense_weight: float = 0.6,
    ) -> None:
        self.repository, self.index = repository, index
        self.embedder, self.reranker = embedder, reranker
        self.max_sources, self.max_objects = max_sources, max_objects
        self.max_fields, self.max_join_hops = max_fields, max_join_hops
        self.max_tokens, self.min_score = max_tokens, min_score
        self.ambiguity_gap, self.dense_weight = ambiguity_gap, dense_weight
        self._validated_manifest_id: str | None = None
        self.budgeter = ContextBudgeter(max_tokens, max_fields)
        self.coverage = CoverageEvaluator(ambiguity_gap=ambiguity_gap)

    async def retrieve(
        self,
        task: TaskFrame,
        permission: PermissionContext,
        schema_gap: SchemaGap | None = None,
        existing_context_id: str | None = None,
        existing_context: GroundedContext | None = None,
    ) -> tuple[GroundedContext, CoverageResult]:
        started = time.perf_counter()
        if not permission.allowed_source_ids:
            raise RuntimeAgentError(
                "PERMISSION_DENIED", "no authorized catalog source is available"
            )
        query = schema_gap.narrow_query if schema_gap else task.question
        version = await self._thread(self.repository.version, permission.allowed_source_ids)
        manifest = await self._thread(self.repository.active_manifest, version)
        provider = str(getattr(self.embedder, "provider", "unknown"))
        model = str(getattr(self.embedder, "model_name", "unknown"))
        if manifest.embedding_provider != provider or manifest.embedding_model != model:
            raise RuntimeAgentError(
                "RAG_EMBEDDING_MODEL_MISMATCH",
                "configured embedding model does not match the active index",
                details={
                    "index_provider": manifest.embedding_provider,
                    "index_model": manifest.embedding_model,
                    "runtime_provider": provider,
                    "runtime_model": model,
                },
            )
        if self._validated_manifest_id != manifest.manifest_id:
            await self._thread(self.index.validate_manifest, manifest)
            self._validated_manifest_id = manifest.manifest_id
        embedding = await self.embedder.embed_query(query)

        pinned_sources = []
        if schema_gap and existing_context and existing_context.objects:
            pinned_sources = list(
                dict.fromkeys(
                    item.source_id
                    for item in existing_context.objects
                    if item.source_id in permission.allowed_source_ids
                )
            )[: self.max_sources]
        if pinned_sources:
            selected_sources, source_hits = pinned_sources, []
        else:
            source_dense = await self._thread(
                self.index.search,
                manifest,
                layer="source_domain",
                embedding=embedding,
                source_ids=permission.allowed_source_ids,
                limit=max(self.max_sources * 2, self.max_sources),
                kinds=["source"],
            )
            source_lexical = await self._thread(
                self.repository.lexical_search,
                query,
                permission,
                max(self.max_sources * 2, self.max_sources),
                layers=("source_domain",),
            )
            source_hits = _fuse(
                source_dense,
                source_lexical,
                dense_weight=self.dense_weight,
                lexical_weight=1 - self.dense_weight,
                limit=self.max_sources,
            )
            selected_sources = [str(item["source_id"]) for item in source_hits]
            if not selected_sources:
                selected_sources = permission.allowed_source_ids[: self.max_sources]
        scoped_permission = permission.model_copy(update={"allowed_source_ids": selected_sources})

        restricted_ids = schema_gap.candidate_object_ids if schema_gap else None
        dense = await self._thread(
            self.index.search,
            manifest,
            layer="object",
            embedding=embedding,
            source_ids=selected_sources,
            limit=self.max_objects * 4,
            object_ids=restricted_ids,
        )
        lexical = await self._thread(
            self.repository.lexical_search,
            query,
            scoped_permission,
            self.max_objects * 4,
            layers=("object",),
            object_ids=restricted_ids,
        )
        fused = _fuse(
            dense,
            lexical,
            dense_weight=self.dense_weight,
            lexical_weight=1 - self.dense_weight,
            limit=self.max_objects * 4,
        )
        metric_hits = [item for item in fused if item["kind"] == "metric"]
        dense_metric_ids: list[str] = []
        if metric_hits and float(metric_hits[0]["score"]) >= self.min_score:
            metric_gap = (
                1.0
                if len(metric_hits) == 1
                else float(metric_hits[0]["score"]) - float(metric_hits[1]["score"])
            )
            if metric_gap >= self.ambiguity_gap:
                dense_metric_ids = [str(metric_hits[0]["target_id"])]
        metric_ids, dimension_ids, required_tables, required_fields = await self._thread(
            self.repository.semantic_bindings,
            query,
            scoped_permission,
            dense_metric_ids=dense_metric_ids,
            proposed_metric_ids=list(task.metric_ids),
            proposed_dimension_ids=list(task.dimension_ids),
        )
        object_hits = [item for item in fused if item["kind"] == "object"]
        seed_ids = [
            str(item["target_id"]) for item in object_hits if float(item["score"]) >= self.min_score
        ]
        object_ids = await self._thread(
            self.repository.expand_object_ids,
            seed_ids,
            scoped_permission,
            self.max_join_hops,
            self.max_objects,
            required_names=required_tables,
        )
        objects, _, _, version = await self._thread(
            self.repository.hydrate, object_ids, scoped_permission
        )
        object_scores = {str(item["target_id"]): float(item["score"]) for item in object_hits}
        objects = [
            item.model_copy(
                update={
                    "score": max(
                        self.min_score if item.name in required_tables else 0,
                        object_scores.get(item.object_id, 0.0),
                    )
                }
            )
            for item in objects
        ]
        ranking, reranker_trace = await self._safe_rerank(query, objects)
        rank_order = {object_id: index for index, object_id in enumerate(ranking)}
        objects.sort(key=lambda item: rank_order.get(item.object_id, len(rank_order)))
        for index, item in enumerate(objects):
            rerank_score = 1 - index / max(1, len(objects))
            objects[index] = item.model_copy(
                update={"score": round(min(1, 0.8 * item.score + 0.2 * rerank_score), 6)}
            )
        object_ids = [item.object_id for item in objects[: self.max_objects]]
        reranked_scores = {item.object_id: item.score for item in objects}

        field_dense = await self._thread(
            self.index.search,
            manifest,
            layer="field_entity",
            embedding=embedding,
            source_ids=selected_sources,
            object_ids=object_ids,
            limit=max(1, self.max_objects * self.max_fields),
            kinds=["field"],
            denied_classifications=permission.denied_classifications,
        )
        field_lexical = await self._thread(
            self.repository.lexical_search,
            query,
            scoped_permission,
            max(1, self.max_objects * self.max_fields),
            layers=("field_entity",),
            object_ids=object_ids,
        )
        field_hits = _fuse(
            field_dense,
            field_lexical,
            dense_weight=self.dense_weight,
            lexical_weight=1 - self.dense_weight,
            limit=max(1, self.max_objects * self.max_fields),
        )
        field_ids = [str(item["target_id"]) for item in field_hits]
        objects, fields, joins, version = await self._thread(
            self.repository.hydrate, object_ids, scoped_permission, field_ids=field_ids
        )
        hybrid_scores = {str(item["target_id"]): float(item["score"]) for item in field_hits}
        objects = [
            item.model_copy(
                update={
                    "score": reranked_scores.get(item.object_id, item.score),
                    "index_version": manifest.index_version,
                }
            )
            for item in objects
        ]
        fields = [
            field.model_copy(
                update={
                    "score": hybrid_scores.get(field.field_id, field.score),
                    "index_version": manifest.index_version,
                }
            )
            for field in fields
        ]
        if schema_gap and existing_context:
            objects = _merge_by_id(
                existing_context.objects, objects, key=lambda item: item.object_id
            )[: self.max_objects]
            fields = _merge_by_id(existing_context.fields, fields, key=lambda item: item.field_id)
            joins = _merge_by_id(existing_context.join_paths, joins, key=lambda item: item.join_id)

        objects, fields, joins, token_count = self.budgeter.apply(
            objects=objects,
            fields=fields,
            joins=joins,
            metrics=metric_ids,
            required_fields=required_fields | set(dimension_ids),
        )
        coverage = self.coverage.evaluate(
            task=task,
            objects=objects,
            fields=fields,
            metric_ids=metric_ids,
            dimension_ids=dimension_ids,
            required_fields=required_fields,
            schema_gap=schema_gap,
            query=query,
        )
        coverage = coverage.model_copy(
            update={
                "confidence_notes": [
                    "permission-first MySQL BM25 + Milvus dense + LLM reranker",
                    f"catalog={version}; index={manifest.index_version}",
                ]
            }
        )
        retrieval_trace = {
            "purpose": "schema_retrieval",
            "provider": provider,
            "model": model,
            "embedding_dimension": len(embedding),
            "catalog_version": version,
            "index_version": manifest.index_version,
            "tokenizer_version": self.budgeter.tokenizer_version,
            "source_candidates": len(source_hits),
            "object_candidates": len(object_hits),
            "field_candidates": len(field_hits),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        context = GroundedContext(
            context_id=(
                existing_context.context_id
                if existing_context
                else existing_context_id or f"ctx_{uuid4().hex[:16]}"
            ),
            catalog_version=version,
            objects=objects,
            fields=fields,
            metrics=metric_ids,
            join_paths=joins,
            coverage=coverage.status,
            token_count=token_count,
            tokenizer_version=self.budgeter.tokenizer_version,
            permission_policy_version=permission.policy_version,
            model_traces=[retrieval_trace, reranker_trace],
        )
        return context, coverage

    async def _safe_rerank(
        self, query: str, objects: list[CatalogObject]
    ) -> tuple[list[str], dict[str, Any]]:
        try:
            return await self.reranker.rerank(query, objects)
        except RuntimeAgentError as exc:
            if exc.error_code not in {"RAG_RERANK_FAILED", "LLM_RESPONSE_INVALID"}:
                raise
            return [item.object_id for item in objects], {
                "purpose": "reranker",
                "fallback": "permission_filtered_order",
                "error_code": exc.error_code,
            }

    @staticmethod
    async def _thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(function, *args, **kwargs)


__all__ = [
    "ContextBudgeter",
    "LLMReranker",
    "PassthroughReranker",
    "ProductionCatalogRetrievalService",
    "build_embedder",
]
