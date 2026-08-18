"""In-process evaluation harness. Results are explicitly non-production."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from ..graph import RuntimeGraph
from ..graph._evidence import build_evaluation_evidence
from ..models import ChatResponse, PermissionContext
from ..repositories.runtime import RuntimePersistence
from ..testing import build_test_permission
from .ablations import run_ablations
from .cases import TIME_ANCHOR, EvalCase
from .compare import compare_results
from .metrics import summarize_metrics
from .reproducibility import build_reproducibility
from .scoring import CaseOutcome, score_case
from .security import run_security_probe


def _expected_status(case: EvalCase) -> str:
    if case.should_clarify:
        return "WAITING_FOR_USER"
    if case.should_reject:
        return "REJECTED"
    return "SUCCEEDED"


def _actions(response: ChatResponse) -> list[str]:
    actions = [
        str(event.get("action"))
        for event in response.events
        if event.get("event") == "node.started" and event.get("action")
    ]
    return [action for action in actions if action and action != "None"]


def _usage(response: ChatResponse) -> tuple[int, int]:
    input_tokens = output_tokens = 0
    for event in response.events:
        usage = event.get("model_usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return input_tokens, output_tokens


def _artifact_types(
    graph: RuntimeGraph,
    response: ChatResponse,
    *,
    user_id: str,
    permission: PermissionContext,
    catalog_version: str,
) -> list[str]:
    types: list[str] = []
    store = graph.persistence
    if store is None or not hasattr(store, "get_artifact_record"):
        return types
    for artifact_id in response.artifact_ids:
        try:
            record = store.get_artifact_record(
                artifact_id, user_id=user_id, permission=permission, catalog_version=catalog_version
            )
        except Exception:
            continue
        spec = record.get("spec") if isinstance(record, dict) else None
        artifact_type = spec.get("type") if isinstance(spec, dict) else None
        if artifact_type:
            types.append(str(artifact_type))
    return types


def _rows(graph: RuntimeGraph, response: ChatResponse) -> tuple[list[str], list[dict[str, Any]]]:
    if not response.result_ids:
        return [], []
    repository = getattr(graph.gateway, "results", None)
    if repository is None:
        return [], []
    page = repository.page(response.result_ids[-1], offset=0, limit=1000)
    rows = list(page.get("rows") or [])
    columns = list(rows[0]) if rows else []
    return columns, rows


def _state_snapshot(graph: RuntimeGraph, response: ChatResponse) -> dict[str, Any]:
    evidence = response.evidence
    if evidence is None and graph.persistence is not None:
        state = graph.persistence.load_state(response.thread_id)
        evidence = build_evaluation_evidence(state) if state is not None else None
    if evidence is None:
        return {}
    return {
        "intent": evidence.intent,
        "metric_ids": list(evidence.metric_ids),
        "objects": list(evidence.object_names),
        "fields": list(evidence.field_names),
        "coverage": evidence.coverage,
        "retrieval_rounds": evidence.retrieval_rounds,
        "grounded_tokens": evidence.grounded_context_tokens,
        "schema_gap_recovered": evidence.schema_gap_recovered,
    }


def _ensure_persistence(graph: RuntimeGraph) -> None:
    if graph.persistence is not None:
        return
    handle = tempfile.NamedTemporaryFile(prefix="eval_", suffix=".db", delete=False)
    handle.close()
    graph.persistence = RuntimePersistence(url=f"sqlite:///{handle.name}", create_schema=True)


def _run_case(case: EvalCase, graph: RuntimeGraph, golden_dir: Path) -> CaseOutcome:
    expected = _expected_status(case)
    if case.deferred_reason:
        return score_case(
            CaseOutcome(
                case_id=case.case_id,
                category=case.category,
                status="NOT_RUN",
                expected_status=expected,
                action_sequence=[],
                expected_action_sequence=list(case.expected_action_sequence),
                expected_intent=case.golden_task_frame.intent.value,
                expected_metric_ids=list(case.golden_task_frame.metric_ids),
                required_objects=list(case.required_objects),
                required_fields=list(case.required_fields),
                deferred=True,
                data_version=case.data_version,
                catalog_version=case.catalog_version,
            )
        )
    permission = build_test_permission(case.user_id)
    started = time.perf_counter()
    response: ChatResponse | None = None
    thread_id = None
    state_version = None
    error_code = None
    try:
        for index, message in enumerate(case.messages):
            resume = (
                index > 0 and response is not None and response.status.value == "WAITING_FOR_USER"
            )
            response = graph.run(
                message=message.content,
                user_id=case.user_id,
                permission=permission,
                thread_id=thread_id,
                expected_state_version=state_version if thread_id else None,
                resume=resume,
            )
            thread_id = response.thread_id
            state_version = response.state_version
            if response.status.value in {"FAILED", "REJECTED", "TIMEOUT"}:
                break
    except Exception as exc:
        error_code = getattr(exc, "error_code", None) or type(exc).__name__
        status = "REJECTED" if error_code == "PERMISSION_DENIED" else "FAILED"
        latency = (time.perf_counter() - started) * 1000
        return score_case(
            CaseOutcome(
                case_id=case.case_id,
                category=case.category,
                status=status,
                expected_status=expected,
                action_sequence=[],
                expected_action_sequence=list(case.expected_action_sequence),
                expected_intent=case.golden_task_frame.intent.value,
                expected_metric_ids=list(case.golden_task_frame.metric_ids),
                required_objects=list(case.required_objects),
                required_fields=list(case.required_fields),
                latency_ms=latency,
                error_code=str(error_code),
                last_action="FAIL",
                result_ok=expected in {"REJECTED", "FAILED"} and status == expected,
                data_version=case.data_version,
                catalog_version=case.catalog_version,
            )
        )
    assert response is not None
    latency = (time.perf_counter() - started) * 1000
    golden = json.loads((golden_dir / case.golden_result_ref).read_text(encoding="utf-8"))
    columns, rows = _rows(graph, response)
    artifacts = _artifact_types(
        graph,
        response,
        user_id=case.user_id,
        permission=permission,
        catalog_version=case.catalog_version,
    )
    snapshot = _state_snapshot(graph, response)
    actions = _actions(response)
    result_ok = compare_results(
        golden,
        observed_columns=columns,
        observed_rows=rows,
        spec=case.result_compare,
        artifact_types=artifacts,
    )
    if case.should_reject:
        result_ok = response.status.value == "REJECTED"
    elif golden.get("status"):
        result_ok = response.status.value == golden["status"]
    sql_ok = (not golden.get("columns")) or set(golden.get("columns") or []) == set(columns)
    input_tokens, output_tokens = _usage(response)
    failure_events = [event for event in response.events if event.get("event") == "run.failed"]
    error_code = error_code or (failure_events[-1].get("error_code") if failure_events else None)
    return score_case(
        CaseOutcome(
            case_id=case.case_id,
            category=case.category,
            status=response.status.value,
            expected_status=expected,
            action_sequence=actions,
            expected_action_sequence=list(case.expected_action_sequence),
            observed_intent=snapshot.get("intent"),
            expected_intent=case.golden_task_frame.intent.value,
            observed_metric_ids=list(snapshot.get("metric_ids") or []),
            expected_metric_ids=list(case.golden_task_frame.metric_ids),
            observed_objects=list(snapshot.get("objects") or []),
            required_objects=list(case.required_objects),
            observed_fields=list(snapshot.get("fields") or []),
            required_fields=list(case.required_fields),
            observed_columns=columns,
            observed_rows=rows,
            artifact_types=artifacts,
            coverage=snapshot.get("coverage"),
            schema_gap_recovered=snapshot.get("schema_gap_recovered"),
            retrieval_rounds=int(snapshot.get("retrieval_rounds") or 0),
            graph_steps=len(actions),
            grounded_context_tokens=snapshot.get("grounded_tokens"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            error_code=error_code,
            last_action=actions[-1] if actions else response.status.value,
            result_ok=result_ok,
            sql_execution_accurate=sql_ok,
            data_version=case.data_version,
            catalog_version=case.catalog_version,
        )
    )


def run_cases(
    cases: list[EvalCase],
    graph: RuntimeGraph,
    *,
    execution_mode: str = "deterministic_test_double",
    golden_dir: str | Path = "tests/golden_results",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_persistence(graph)
    golden_path = Path(golden_dir)
    outcomes = [_run_case(case, graph, golden_path) for case in cases]
    filter_note = (
        "test_double runnable cases; not a production Task Completion Rate"
        if execution_mode != "production_runtime"
        else "runnable cases"
    )
    metrics = summarize_metrics(outcomes, filter_note=filter_note)
    failures = [
        item.failure_record() for item in outcomes if not item.deferred and not item.completed
    ]
    report: dict[str, Any] = {
        "evaluation_mode": execution_mode,
        "non_production": execution_mode != "production_runtime",
        "data_version": "seed_v1",
        "catalog_version": "catalog_v1",
        "case_count": len(outcomes),
        "metrics": metrics,
        "security_probe": run_security_probe(),
        "ablations": run_ablations(),
        "cases": [item.as_row() for item in outcomes],
        "failure_cases": failures[:8],
        "trace_samples": [
            {"case_id": item.case_id, "action_sequence": item.action_sequence}
            for item in outcomes
            if not item.deferred
        ][:5],
        "reproducibility": build_reproducibility(
            command="python3.12 scripts/run_evaluation.py --allow-test-double",
            execution_mode=execution_mode,
            data_version="seed_v1",
            catalog_version="catalog_v1",
            settings=settings,
        ),
        "time_anchor": TIME_ANCHOR,
    }
    if execution_mode != "production_runtime":
        completed = [item for item in outcomes if not item.deferred]
        passed = sum(1 for item in completed if item.completed)
        report["test_double_task_completion_rate"] = passed / len(completed) if completed else 0.0
        report["security_pass_rate"] = report["security_probe"]["pass_rate"]
    return report
