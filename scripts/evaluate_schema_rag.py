#!/usr/bin/env python3
"""Evaluate real production Schema RAG against the 70-case linking fixture."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.auth import Principal
from backend.app.bootstrap import build_runtime_container
from backend.app.config import load_settings
from backend.app.models import TaskFrame
from backend.app.services.catalog_retrieval import ProductionCatalogRetrievalService


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


class IdentityReranker:
    async def rerank(self, query, objects):
        return [item.object_id for item in objects], {
            "purpose": "reranker", "disabled_for_ablation": True}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--cases", type=Path,
                        default=Path("tests/eval_cases/schema_catalog.json"))
    parser.add_argument("--report", type=Path,
                        default=Path("reports/schema-rag-evaluation.json"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--disable-reranker", action="store_true")
    parser.add_argument("--dense-weight", type=float, default=0.6)
    return parser.parse_args()


async def main() -> int:
    args = arguments()
    if not 0 <= args.dense_weight <= 1:
        raise SystemExit("--dense-weight must be between 0 and 1")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.limit is not None:
        cases = cases[:max(0, args.limit)]
    container = build_runtime_container(load_settings(args.config))
    if container.rag_error:
        raise SystemExit(f"production Schema RAG is unavailable: {container.rag_error}")
    permission = container.permissions.for_principal(
        Principal("u_demo_user", ("USER",)))
    reranker = IdentityReranker() if args.disable_reranker else container.retrieval.reranker
    budget = container.settings.retrieval_budget
    retrieval = ProductionCatalogRetrievalService(
        container.catalog_repository, container.catalog_index, container.embedder,
        reranker,
        max_sources=int(budget.get("max_source_candidates", 3)),
        max_objects=int(budget.get("max_object_candidates", 5)),
        max_fields=int(budget.get("max_fields_per_object", 8)),
        max_join_hops=int(budget.get("max_join_hops", 2)),
        max_tokens=int(budget.get("max_context_tokens", 3000)),
        min_score=float(budget.get("min_rerank_score", .55)),
        ambiguity_gap=float(budget.get("ambiguity_score_gap", .08)),
        dense_weight=args.dense_weight)
    outcomes: list[dict[str, Any]] = []
    try:
        for case in cases:
            question = case["messages"][-1]["content"]
            task = TaskFrame(
                task_id=f"rag_{case['case_id']}", user_id=permission.user_id,
                question=question,
                intent=case.get("golden_task_frame", {}).get("intent", "SCHEMA_QUERY"))
            started = time.perf_counter()
            context, coverage = await retrieval.retrieve(task, permission)
            latency = round((time.perf_counter() - started) * 1000, 2)
            expected_objects = set(case.get("required_objects", []))
            expected_fields = set(case.get("required_fields", []))
            observed_objects = {item.name for item in context.objects}
            observed_fields = {item.name for item in context.fields}
            object_recall = (len(expected_objects & observed_objects) / len(expected_objects)
                             if expected_objects else 1.0)
            field_recall = (len(expected_fields & observed_fields) / len(expected_fields)
                            if expected_fields else 1.0)
            relevant_observed = len(expected_objects & observed_objects) + len(
                expected_fields & observed_fields)
            observed_total = len(observed_objects) + len(observed_fields)
            context_precision = relevant_observed / observed_total if observed_total else 0
            sensitive = [item.name for item in context.fields
                         if item.classification in permission.denied_classifications]
            passed = (object_recall == 1 and field_recall == 1 and not sensitive
                      and context.token_count <= int(budget.get("max_context_tokens", 3000)))
            outcomes.append({
                "case_id": case["case_id"], "passed": passed,
                "coverage": coverage.status.value,
                "object_recall_at_k": object_recall,
                "field_recall_at_k": field_recall,
                "context_precision": round(context_precision, 6),
                "object_count": len(observed_objects), "field_count": len(observed_fields),
                "context_tokens": context.token_count, "latency_ms": latency,
                "sensitive_field_count": len(sensitive),
            })
        object_recalls = [item["object_recall_at_k"] for item in outcomes]
        field_recalls = [item["field_recall_at_k"] for item in outcomes]
        precisions = [item["context_precision"] for item in outcomes]
        tokens = [item["context_tokens"] for item in outcomes]
        latencies = [item["latency_ms"] for item in outcomes]
        manifest = container.catalog_repository.active_manifest()
        report = {
            "mode": "production_schema_rag",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(outcomes),
            "passed": sum(bool(item["passed"]) for item in outcomes),
            "catalog_version": manifest.catalog_version,
            "index_version": manifest.index_version,
            "embedding_provider": manifest.embedding_provider,
            "embedding_model": manifest.embedding_model,
            "embedding_dimension": manifest.embedding_dimension,
            "reranker_enabled": not args.disable_reranker,
            "dense_weight": args.dense_weight,
            "metrics": {
                "object_recall_at_k": round(statistics.mean(object_recalls), 6)
                if object_recalls else 0,
                "field_recall_at_k": round(statistics.mean(field_recalls), 6)
                if field_recalls else 0,
                "context_precision": round(statistics.mean(precisions), 6)
                if precisions else 0,
                "p95_context_tokens": percentile(tokens, .95),
                "max_context_tokens": max(tokens, default=0),
                "p95_latency_ms": percentile(latencies, .95),
                "mean_latency_ms": round(statistics.mean(latencies), 2)
                if latencies else 0,
                "sensitive_candidate_leaks": sum(
                    item["sensitive_field_count"] for item in outcomes),
            },
            "outcomes": outcomes,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(f"evaluated {len(outcomes)} schema-linking cases; "
              f"passed={report['passed']}; report={args.report}")
        return 0 if report["passed"] == len(outcomes) else 1
    finally:
        await container.llm.aclose()
        if container.embedder:
            await container.embedder.aclose()
        if container.catalog_index:
            container.catalog_index.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
