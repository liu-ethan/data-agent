from __future__ import annotations

import json
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Protocol

import yaml

from backend.app.catalog.models import CatalogSnapshot, MetricSpec, TableRelation
from backend.app.retrieval import bm25, vector
from backend.app.retrieval.bm25 import Hit
from backend.app.types import (
    Ambiguous,
    QueryTask,
    RuntimeContext,
    SchemaBundle,
    SchemaGap,
)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt" / "retrieval.yaml"
_RRF_K = 60


class RetrievalLlm(Protocol):
    def table_queries(self, task: QueryTask, prompt: str) -> list[str]: ...

    def schema_gap(
        self,
        *,
        missing_concept: str,
        purpose: str,
        constraints: list[str],
        excluded: list[str],
        prompt: str,
    ) -> SchemaGap: ...


def _load_prompts() -> dict[str, str]:
    data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("retrieval.yaml must be a mapping")
    return {str(k): str(v) for k, v in data.items()}


def _column_allowed(table: str, column: str, allowed: list[str]) -> bool:
    keys = {
        f"data-agent-ecommerce.{table}.{column}",
        f"data-agent-ecommerce.{table}.*",
        f"{table}.{column}",
        f"{table}.*",
        "*",
    }
    return any(item in keys for item in allowed)


def _filter_catalog(catalog: CatalogSnapshot, ctx: RuntimeContext) -> CatalogSnapshot:
    perms = ctx.permissions
    tables = [t for t in catalog.tables if t.table_name in perms.allowed_tables]
    allowed_tables = {t.table_name for t in tables}
    columns = [
        c
        for c in catalog.columns
        if c.table_name in allowed_tables
        and _column_allowed(c.table_name, c.column_name, perms.allowed_columns)
    ]
    relations = [
        r
        for r in catalog.relations
        if r.left_table in allowed_tables and r.right_table in allowed_tables
    ]
    metrics = [m for m in catalog.metrics if m.metric_id in perms.allowed_metrics]
    return catalog.model_copy(
        update={
            "tables": tables,
            "columns": columns,
            "relations": relations,
            "metrics": metrics,
        }
    )


def _required_fields(task: QueryTask, metrics: list[MetricSpec]) -> list[str]:
    fields: list[str] = []
    for metric in metrics:
        fields.extend(metric.deps)
        fields.append(metric.time_field)
        fields.extend(cond.field for cond in metric.filters)
    fields.extend(task.dimensions)
    fields.extend(cond.field for cond in task.filters)
    seen: list[str] = []
    for field in fields:
        if field and "." in field and field not in seen:
            seen.append(field)
    return seen


def _required_tables(metrics: list[MetricSpec], fields: list[str]) -> set[str]:
    tables = {field.split(".", 1)[0] for field in fields}
    for metric in metrics:
        tables.add(metric.grain_table)
        tables.update(metric.needs_tables)
    return tables


def _rrf(hit_lists: list[list[Hit]]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.key] += 1.0 / (_RRF_K + rank)
    return dict(scores)


def _top_keys(scores: dict[str, float], top_k: int) -> list[str]:
    return [key for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]]


def _hybrid_tables(
    queries: list[str],
    catalog: CatalogSnapshot,
    *,
    top_k: int,
    embedder: vector.Embedder | None,
    embeddings_db: str | Path | None,
) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for query in queries:
        bm25_hits = bm25.search_tables(query, catalog.tables, top_k)
        vec_hits: list[Hit] = []
        if embedder is not None and embeddings_db is not None:
            vec_hits = vector.search_tables(
                query,
                catalog_version=catalog.catalog_version,
                embeddings_db=embeddings_db,
                embedder=embedder,
                top_k=top_k,
            )
        for key, score in _rrf([bm25_hits, vec_hits]).items():
            scores[key] += score
    return _top_keys(dict(scores), top_k)


def _hybrid_columns(
    queries: list[str],
    catalog: CatalogSnapshot,
    *,
    top_k: int,
    table_names: set[str] | None,
    embedder: vector.Embedder | None,
    embeddings_db: str | Path | None,
) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for query in queries:
        bm25_hits = bm25.search_columns(query, catalog.columns, top_k, table_names=table_names)
        vec_hits: list[Hit] = []
        if embedder is not None and embeddings_db is not None:
            vec_hits = vector.search_columns(
                query,
                catalog_version=catalog.catalog_version,
                embeddings_db=embeddings_db,
                embedder=embedder,
                top_k=top_k,
                table_names=table_names,
            )
        for key, score in _rrf([bm25_hits, vec_hits]).items():
            scores[key] += score
    return _top_keys(dict(scores), top_k)


def _undirected(relations: list[TableRelation]) -> dict[str, list[tuple[str, TableRelation]]]:
    graph: dict[str, list[tuple[str, TableRelation]]] = defaultdict(list)
    for rel in relations:
        graph[rel.left_table].append((rel.right_table, rel))
        graph[rel.right_table].append((rel.left_table, rel))
    return graph


def _simple_paths(
    graph: dict[str, list[tuple[str, TableRelation]]],
    src: str,
    dst: str,
    allowed: set[str] | None,
) -> list[list[str]]:
    paths: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if allowed is not None and node not in allowed:
            return
        if node == dst and len(path) > 1:
            paths.append(list(path))
            return
        for nxt, _rel in graph.get(node, []):
            if nxt in path:
                continue
            if allowed is not None and nxt not in allowed:
                continue
            path.append(nxt)
            dfs(nxt, path)
            path.pop()

    dfs(src, [src])
    return paths


def _join_dict(rel: TableRelation) -> dict[str, str]:
    return {
        "left": rel.left_table,
        "right": rel.right_table,
        "on_left": rel.left_col,
        "on_right": rel.right_col,
        "cardinality": rel.cardinality,
    }


def _edges_on_path(
    path: list[str], graph: dict[str, list[tuple[str, TableRelation]]]
) -> list[TableRelation]:
    edges: list[TableRelation] = []
    for left, right in pairwise(path):
        for nxt, rel in graph[left]:
            if nxt == right:
                edges.append(rel)
                break
    return edges


def _complete_joins(
    required: set[str],
    grain_table: str,
    relations: list[TableRelation],
) -> Ambiguous | list[TableRelation] | None:
    if not required:
        return []
    graph = _undirected(relations)
    root = grain_table if grain_table in required else min(required)
    used: list[TableRelation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for table in sorted(required):
        if table == root:
            continue
        induced = _simple_paths(graph, root, table, required)
        if len(induced) >= 2:
            return Ambiguous(
                reason=f"multiple reviewed join paths between {root} and {table}",
                paths=induced,
            )
        chosen: list[str] | None = induced[0] if len(induced) == 1 else None
        if chosen is None:
            full = _simple_paths(graph, root, table, None)
            if len(full) >= 2:
                return Ambiguous(
                    reason=f"multiple reviewed join paths between {root} and {table}",
                    paths=full,
                )
            if not full:
                return None
            chosen = full[0]
        for rel in _edges_on_path(chosen, graph):
            key = (rel.left_table, rel.left_col, rel.right_table, rel.right_col)
            if key not in seen:
                seen.add(key)
                used.append(rel)
    return used


def _present_columns(fields: list[str], catalog: CatalogSnapshot, candidates: set[str]) -> list[str]:
    available = {(c.table_name, c.column_name) for c in catalog.columns}
    have: list[str] = []
    for field in fields:
        table, column = field.split(".", 1)
        if table in candidates and (table, column) in available:
            have.append(field)
    return have


def _default_table_queries(task: QueryTask, metrics: list[MetricSpec]) -> list[str]:
    queries = list(task.metric_ids)
    queries.extend(metric.name for metric in metrics)
    queries.extend(metric.grain_table for metric in metrics)
    queries.extend(task.dimensions)
    queries.extend(cond.field for cond in task.filters)
    return [q for q in queries if q]


def _make_gap(
    llm: RetrievalLlm | None,
    prompts: dict[str, str],
    *,
    missing: list[str],
    excluded: list[str],
) -> SchemaGap:
    concept = ", ".join(missing) if missing else "schema coverage"
    if llm is not None:
        try:
            return llm.schema_gap(
                missing_concept=concept,
                purpose="query_coverage",
                constraints=list(missing),
                excluded=excluded,
                prompt=prompts["schema_gap"],
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return SchemaGap(
        missing_concept=concept,
        purpose="query_coverage",
        constraints=list(missing),
        excluded=excluded,
    )


def retrieve_schema(
    task: QueryTask,
    ctx: RuntimeContext,
    catalog: CatalogSnapshot,
    *,
    llm: RetrievalLlm | None = None,
    embedder: vector.Embedder | None = None,
    embeddings_db: str | Path | None = None,
    table_top_k: int = 5,
    column_top_k: int = 10,
    max_gap_rounds: int = 2,
) -> SchemaBundle | SchemaGap | Ambiguous:
    catalog = _filter_catalog(catalog, ctx)
    metrics = [m for m in catalog.metrics if m.metric_id in task.metric_ids]
    missing_metrics = [mid for mid in task.metric_ids if mid not in {m.metric_id for m in metrics}]
    prompts = _load_prompts()
    if missing_metrics:
        return SchemaGap(
            missing_concept=", ".join(missing_metrics),
            purpose="metric",
            constraints=missing_metrics,
            excluded=[],
        )

    if embedder is not None and embeddings_db is not None:
        vector.ensure_index(catalog, embeddings_db=embeddings_db, embedder=embedder)

    if llm is not None:
        try:
            queries = llm.table_queries(task, prompts["table_queries"])
        except (json.JSONDecodeError, ValueError, TypeError):
            queries = []
    else:
        queries = _default_table_queries(task, metrics)
    if not queries:
        queries = _default_table_queries(task, metrics)

    required_fields = _required_fields(task, metrics)
    required_tables = _required_tables(metrics, required_fields)
    catalog_tables = {t.table_name for t in catalog.tables}
    required_tables &= catalog_tables
    candidates = set(
        _hybrid_tables(
            queries,
            catalog,
            top_k=table_top_k,
            embedder=embedder,
            embeddings_db=embeddings_db,
        )
    )
    candidates |= required_tables
    grain = metrics[0].grain_table if metrics else (min(required_tables) if required_tables else "")

    for round_i in range(max_gap_rounds + 1):
        _hybrid_columns(
            queries,
            catalog,
            top_k=column_top_k,
            table_names=candidates,
            embedder=embedder,
            embeddings_db=embeddings_db,
        )
        have = _present_columns(required_fields, catalog, candidates)
        missing = [field for field in required_fields if field not in have]
        tables_ready = required_tables <= candidates
        if tables_ready:
            joins = _complete_joins(required_tables, grain, catalog.relations)
            if isinstance(joins, Ambiguous):
                return joins
            if joins is not None and not missing:
                join_dicts = [_join_dict(rel) for rel in joins]
                join_dicts.sort(key=lambda j: (j["left"], j["right"], j["on_left"]))
                return SchemaBundle(
                    tables=sorted(required_tables),
                    columns=list(have),
                    joins=join_dicts,
                    catalog_version=catalog.catalog_version,
                )
            if joins is None:
                missing = [*missing, "join_path"]

        gap_missing = missing or sorted(required_tables - candidates)
        if round_i == max_gap_rounds:
            return _make_gap(llm, prompts, missing=gap_missing, excluded=sorted(candidates))

        gap = _make_gap(llm, prompts, missing=gap_missing, excluded=sorted(candidates))
        gap_queries = [gap.missing_concept, *gap.constraints, *missing]
        new_keys = _hybrid_columns(
            [q for q in gap_queries if q],
            catalog,
            top_k=column_top_k,
            table_names=None,
            embedder=embedder,
            embeddings_db=embeddings_db,
        )
        added = {key.split(".", 1)[0] for key in new_keys} - candidates
        added &= catalog_tables
        if not added:
            return gap
        candidates |= added

    return _make_gap(
        llm,
        prompts,
        missing=sorted(required_tables - candidates),
        excluded=sorted(candidates),
    )
