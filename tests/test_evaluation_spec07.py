"""Spec 07 acceptance tests for evaluation cases, metrics, ablations and reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.evaluation import (
    TIME_ANCHOR,
    EvalCase,
    compare_results,
    load_cases,
    metric_definitions,
    run_ablations,
    run_security_probe,
    score_case,
    summarize_metrics,
    write_report,
)
from backend.app.evaluation.harness import CaseOutcome, run_cases
from backend.app.evaluation.observe import evidence_from_payload
from backend.app.evaluation.reproducibility import build_reproducibility
from backend.app.models import RuntimeEvent
from backend.app.testing import build_test_permission, build_test_runtime

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "tests" / "eval_cases"
GOLDEN_DIR = ROOT / "tests" / "golden_results"


def _outcome(**updates) -> CaseOutcome:
    values = dict(
        case_id="eval_001",
        category="single_turn_data_query",
        status="SUCCEEDED",
        expected_status="SUCCEEDED",
        action_sequence=["RETRIEVE", "GENERATE", "EXECUTE", "RESPOND"],
        expected_action_sequence=["RETRIEVE", "GENERATE", "EXECUTE", "RESPOND"],
        observed_intent="DATA_QUERY",
        expected_intent="DATA_QUERY",
        observed_metric_ids=["gmv"],
        expected_metric_ids=["gmv"],
        observed_objects=["orders", "order_items"],
        required_objects=["orders", "order_items"],
        observed_fields=["orders.paid_at", "order_items.item_paid_amount"],
        required_fields=["orders.paid_at", "order_items.item_paid_amount"],
        observed_columns=["gmv"],
        observed_rows=[{"gmv": 10.0}],
        artifact_types=["RESULT_TABLE"],
        coverage="SUFFICIENT",
        schema_gap_recovered=False,
        retrieval_rounds=1,
        graph_steps=4,
        grounded_context_tokens=400,
        input_tokens=120,
        output_tokens=40,
        latency_ms=80.0,
        error_code=None,
        last_action="RESPOND",
        result_ok=True,
        deferred=False,
        sql_execution_accurate=True,
    )
    values.update(updates)
    return CaseOutcome(**values)


def test_eval_case_rejects_single_message_standin_for_multi_turn():
    payload = {
        "case_id": "eval_bad",
        "category": "follow_up",
        "user_id": "u_demo_user",
        "messages": [{"role": "user", "content": "刚才那个呢"}],
        "golden_task_frame": {"intent": "DATA_QUERY"},
        "required_objects": ["orders"],
        "required_fields": ["orders.paid_at"],
        "expected_action_sequence": ["RETRIEVE", "RESPOND"],
        "golden_result_ref": "eval_bad_result.json",
        "should_clarify": False,
        "should_reject": False,
        "budgets": {"max_steps": 6, "max_retrieval_rounds": 2, "max_seconds": 30},
        "data_version": "seed_v1",
        "catalog_version": "catalog_v1",
        "result_compare": {
            "row_order": "explicit",
            "numeric_abs_tolerance": 0.01,
            "numeric_rel_tolerance": 0.0001,
            "null_equals_zero": False,
        },
        "schema_version": "eval_case_v1",
        "requires_prior_turn": True,
    }
    with pytest.raises(ValueError, match="messages"):
        EvalCase.model_validate(payload)


def test_eval_case_requires_non_empty_grounding_and_snapshot():
    with pytest.raises(ValueError):
        EvalCase.model_validate(
            {
                "case_id": "eval_empty",
                "category": "x",
                "user_id": "u",
                "messages": [{"role": "user", "content": "hi"}],
                "golden_task_frame": {"intent": "DATA_QUERY"},
                "required_objects": [],
                "required_fields": [],
                "expected_action_sequence": [],
                "golden_result_ref": "",
                "budgets": {"max_steps": 6, "max_retrieval_rounds": 2, "max_seconds": 30},
                "data_version": "seed_v1",
                "catalog_version": "catalog_v1",
                "result_compare": {
                    "row_order": "explicit",
                    "numeric_abs_tolerance": 0.01,
                    "numeric_rel_tolerance": 0.0001,
                    "null_equals_zero": False,
                },
                "schema_version": "eval_case_v1",
            }
        )


def test_fixed_eval_cases_match_contract_and_land_in_80_to_100_runnable():
    cases = load_cases(CASES_DIR)
    runnable = [case for case in cases if not case.deferred_reason]
    assert 80 <= len(runnable) <= 100
    categories = {case.category for case in runnable}
    assert "security" in categories
    data_query = [
        case
        for case in runnable
        if case.category
        in {
            "single_turn_data_query",
            "metric_query",
            "refund_query",
            "empty_result",
            "multi_step_data_query",
        }
    ]
    multi_turn = [
        case
        for case in runnable
        if case.category in {"follow_up", "multi_turn", "checkpoint", "long_term_memory"}
    ]
    schema_catalog = [case for case in runnable if case.category == "schema_catalog"]
    assert len(data_query) >= 20
    assert len(multi_turn) >= 12
    assert len(schema_catalog) <= 25
    hitl = [case for case in cases if case.category == "hitl"]
    assert len(hitl) >= 8
    assert all(case.deferred_reason is None for case in hitl)
    for case in cases:
        assert case.schema_version == "eval_case_v1"
        assert case.messages
        assert case.golden_task_frame.intent
        assert case.required_objects
        assert case.required_fields
        assert case.expected_action_sequence
        assert case.golden_result_ref
        assert (GOLDEN_DIR / case.golden_result_ref).is_file()
        assert case.data_version == "seed_v1"
        assert case.catalog_version == "catalog_v1"
        assert case.budgets.max_steps >= 1
        if case.category in {"follow_up", "multi_turn", "checkpoint", "hitl"}:
            assert len(case.messages) >= 2


def test_result_compare_uses_columns_rows_and_numeric_tolerance():
    spec = {
        "row_order": "explicit",
        "numeric_abs_tolerance": 0.01,
        "numeric_rel_tolerance": 0.0001,
        "null_equals_zero": False,
    }
    golden = {
        "columns": ["category_name", "gmv"],
        "rows": [{"category_name": "鞋", "gmv": 10.00}, {"category_name": "服", "gmv": 3.33}],
    }
    assert compare_results(
        golden,
        observed_columns=["gmv", "category_name"],
        observed_rows=[
            {"category_name": "鞋", "gmv": 10.004},
            {"category_name": "服", "gmv": 3.33},
        ],
        spec=spec,
    )
    assert not compare_results(
        golden,
        observed_columns=["category_name", "gmv"],
        observed_rows=[{"category_name": "服", "gmv": 3.33}, {"category_name": "鞋", "gmv": 10.00}],
        spec=spec,
    )
    assert not compare_results(
        golden,
        observed_columns=["category_name", "gmv"],
        observed_rows=[{"category_name": "鞋", "gmv": 10.00}, {"category_name": "服", "gmv": None}],
        spec=spec,
    )


def test_result_compare_any_order_and_null_equals_zero():
    golden = {"columns": ["gmv"], "rows": [{"gmv": 0}]}
    spec = {
        "row_order": "any",
        "numeric_abs_tolerance": 0.01,
        "numeric_rel_tolerance": 0.0001,
        "null_equals_zero": True,
    }
    assert compare_results(
        golden, observed_columns=["gmv"], observed_rows=[{"gmv": None}], spec=spec
    )


def test_task_completion_requires_status_permission_and_result():
    completed = score_case(_outcome())
    assert completed.completed is True
    rejected_ok = score_case(
        _outcome(
            status="REJECTED",
            expected_status="REJECTED",
            result_ok=True,
            action_sequence=["RETRIEVE"],
            expected_action_sequence=["RETRIEVE"],
            sql_execution_accurate=False,
        )
    )
    assert rejected_ok.completed is True
    sql_only = score_case(
        _outcome(
            status="SUCCEEDED",
            expected_status="SUCCEEDED",
            result_ok=False,
            sql_execution_accurate=True,
            observed_columns=["gmv"],
        )
    )
    assert sql_only.completed is False
    assert sql_only.sql_execution_accurate is True


def test_metrics_publish_numerator_denominator_and_filter():
    outcomes = [
        score_case(_outcome()),
        score_case(
            _outcome(
                case_id="eval_010",
                category="security",
                status="REJECTED",
                expected_status="REJECTED",
                result_ok=True,
                action_sequence=["RETRIEVE"],
                expected_action_sequence=["RETRIEVE"],
                sql_execution_accurate=False,
            )
        ),
        score_case(
            _outcome(
                case_id="eval_hitl",
                category="hitl",
                deferred=True,
                status="NOT_RUN",
                expected_status="WAITING_FOR_USER",
                result_ok=False,
                sql_execution_accurate=False,
            )
        ),
    ]
    metrics = summarize_metrics(outcomes)
    names = {item["name"] for item in metrics}
    required = {
        "task_completion_rate",
        "task_frame_accuracy",
        "object_recall_at_k",
        "field_recall_at_k",
        "context_precision",
        "schema_gap_recovery",
        "result_accuracy",
        "action_routing_accuracy",
        "average_graph_steps",
        "security_pass_rate",
        "hitl_resume_success",
        "follow_up_resolution_accuracy",
        "checkpoint_recovery_success",
        "long_term_memory_precision",
        "p95_latency_ms_success",
        "p95_latency_ms_failure",
        "average_token_cost",
        "p95_grounded_context_tokens",
        "sql_execution_accuracy",
    }
    assert required <= names
    for item in metrics:
        assert "numerator" in item and "denominator" in item and "filter" in item
    tcr = next(item for item in metrics if item["name"] == "task_completion_rate")
    assert tcr["denominator"] == 2
    assert tcr["numerator"] == 2
    hitl = next(item for item in metrics if item["name"] == "hitl_resume_success")
    assert hitl["denominator"] == 0
    assert hitl["value"] is None
    sql_vs_tcr = next(item for item in metrics if item["name"] == "sql_execution_accuracy")
    assert sql_vs_tcr["value"] != tcr["value"] or sql_vs_tcr["numerator"] != tcr["numerator"]


def test_ablations_cover_the_five_required_comparisons():
    report = run_ablations()
    required = {
        "full_schema_injection_vs_bounded_context",
        "bm25_vs_hybrid_retrieval",
        "schema_gap_disabled_vs_enabled",
        "full_history_vs_summary_projection",
        "sql_execution_accuracy_vs_task_completion_rate",
    }
    assert required <= set(report)
    full = report["full_schema_injection_vs_bounded_context"]
    assert full["bounded_context_tokens"] < full["full_schema_estimated_tokens"]
    hybrid = report["bm25_vs_hybrid_retrieval"]
    assert hybrid["hybrid"]["object_recall_at_k"] >= hybrid["bm25_only"]["object_recall_at_k"]
    gap = report["schema_gap_disabled_vs_enabled"]
    assert gap["enabled"]["recovered"] is True
    assert gap["disabled"]["recovered"] is False
    prompt = report["full_history_vs_summary_projection"]
    assert prompt["projected_tokens"] < prompt["full_history_tokens"]
    assert prompt["projected_contains_raw_rows"] is False
    contrast = report["sql_execution_accuracy_vs_task_completion_rate"]
    assert contrast["sql_execution_accuracy"]["value"] != contrast["task_completion_rate"]["value"]


def test_security_probe_rejects_dangerous_sql():
    probe = run_security_probe()
    assert probe["case_count"] >= 30
    assert probe["pass_rate"] == 1.0


def test_report_records_reproducibility_and_failure_case_evidence(tmp_path):
    outcomes = [
        score_case(_outcome()),
        score_case(
            _outcome(
                case_id="eval_fail",
                status="FAILED",
                expected_status="SUCCEEDED",
                result_ok=False,
                error_code="SQL_OBJECT_NOT_ALLOWED",
                last_action="EXECUTE",
                sql_execution_accurate=False,
            )
        ),
    ]
    reproducibility = build_reproducibility(
        command="python3.12 scripts/run_evaluation.py --allow-test-double",
        execution_mode="deterministic_test_double",
        data_version="seed_v1",
        catalog_version="catalog_v1",
    )
    assert reproducibility["time_anchor"] == TIME_ANCHOR
    assert reproducibility["timezone"] == "Asia/Shanghai"
    assert reproducibility["random_seed"] == 0
    assert "code_version" in reproducibility
    assert "llm" in reproducibility
    json_path, csv_path = write_report(
        {
            "evaluation_mode": "deterministic_test_double",
            "non_production": True,
            "metrics": summarize_metrics(outcomes),
            "ablations": {"sql_execution_accuracy_vs_task_completion_rate": {}},
            "cases": [item.as_row() for item in outcomes],
            "failure_cases": [item.failure_record() for item in outcomes if not item.completed],
            "trace_samples": [
                {"case_id": item.case_id, "action_sequence": item.action_sequence}
                for item in outcomes[:5]
            ],
            "reproducibility": reproducibility,
        },
        tmp_path,
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["non_production"] is True
    assert report["reproducibility"]["command"]
    assert report["reproducibility"]["tokenizer_version"]
    failures = report["failure_cases"]
    assert 1 <= len(failures) <= 8
    record = failures[0]
    assert record["error_code"] == "SQL_OBJECT_NOT_ALLOWED"
    assert record["last_action"] == "EXECUTE"
    assert record["reproduce_command"].endswith("eval_fail")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "eval_001" in csv_text
    names = {item["name"] for item in metric_definitions()}
    assert "task_completion_rate" in names


def test_in_process_harness_does_not_claim_production_quality(tmp_path):
    fixture = tmp_path / "cases"
    fixture.mkdir()
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "local_result.json").write_text(
        json.dumps({"type": "FIELD_LIST", "source": "orders"}), encoding="utf-8"
    )
    (fixture / "one.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "eval_local_schema",
                    "category": "schema_lookup",
                    "user_id": "u_demo_user",
                    "messages": [{"role": "user", "content": "orders 表有哪些字段？"}],
                    "golden_task_frame": {"intent": "SCHEMA_LOOKUP"},
                    "required_objects": ["orders"],
                    "required_fields": ["orders.order_id"],
                    "expected_action_sequence": ["RETRIEVE", "RESPOND"],
                    "golden_result_ref": "local_result.json",
                    "should_clarify": False,
                    "should_reject": False,
                    "budgets": {"max_steps": 6, "max_retrieval_rounds": 2, "max_seconds": 30},
                    "data_version": "seed_v1",
                    "catalog_version": "catalog_v1",
                    "result_compare": {
                        "row_order": "explicit",
                        "numeric_abs_tolerance": 0.01,
                        "numeric_rel_tolerance": 0.0001,
                        "null_equals_zero": False,
                    },
                    "schema_version": "eval_case_v1",
                }
            ]
        ),
        encoding="utf-8",
    )
    report = run_cases(
        load_cases(fixture),
        build_test_runtime(),
        golden_dir=golden,
        execution_mode="deterministic_test_double",
    )
    assert report["non_production"] is True
    assert "test_double_task_completion_rate" in report
    assert "task_completion_rate" not in {
        key for key in report if not isinstance(report.get(key), list)
    }
    tcr = next(item for item in report["metrics"] if item["name"] == "task_completion_rate")
    assert "test_double" in tcr["filter"]


def test_evaluation_evidence_is_read_from_chat_payload_or_terminal_event():
    from_body = evidence_from_payload(
        {
            "status": "SUCCEEDED",
            "evidence": {
                "intent": "DATA_QUERY",
                "metric_ids": ["gmv"],
                "object_names": ["orders", "order_items"],
                "field_names": ["orders.paid_at"],
                "coverage": "SUFFICIENT",
                "retrieval_rounds": 1,
                "grounded_context_tokens": 512,
                "schema_gap_recovered": None,
            },
            "events": [],
        }
    )
    assert from_body["intent"] == "DATA_QUERY"
    assert from_body["metric_ids"] == ["gmv"]
    assert from_body["objects"] == ["orders", "order_items"]
    assert from_body["fields"] == ["orders.paid_at"]
    assert from_body["grounded_tokens"] == 512
    assert from_body["schema_gap_recovered"] is None
    from_event = evidence_from_payload(
        {
            "status": "SUCCEEDED",
            "events": [
                {"event": "node.completed", "action": "RETRIEVE"},
                {
                    "event": "run.completed",
                    "evidence": {
                        "intent": "SCHEMA_LOOKUP",
                        "metric_ids": [],
                        "object_names": ["orders"],
                        "field_names": ["orders.order_id"],
                        "coverage": "SUFFICIENT",
                        "retrieval_rounds": 2,
                        "grounded_context_tokens": 220,
                        "schema_gap_recovered": True,
                    },
                },
            ],
        }
    )
    assert from_event["intent"] == "SCHEMA_LOOKUP"
    assert from_event["schema_gap_recovered"] is True
    assert from_event["retrieval_rounds"] == 2


def test_chat_response_publishes_evaluation_evidence_for_production_scoring():
    response = build_test_runtime().run(
        message="orders 表有哪些字段？",
        user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
    )
    evidence = response.evidence
    assert evidence is not None
    assert evidence.intent in {"SCHEMA_LOOKUP", "SCHEMA_QUERY"}
    assert "orders" in evidence.object_names
    assert any(name.startswith("orders.") for name in evidence.field_names)
    assert evidence.grounded_context_tokens is not None
    assert evidence.grounded_context_tokens > 0
    assert evidence.retrieval_rounds >= 1
    assert evidence.schema_gap_recovered is None
    terminal = [
        event for event in response.events if event.get("event") in {"run.completed", "run.failed"}
    ]
    assert terminal
    published = terminal[-1].get("evidence") or {}
    assert "orders" in published.get("object_names", [])
    RuntimeEvent.model_validate(terminal[-1])


def test_identity_mismatch_is_excluded_from_task_completion_denominator():
    metrics = summarize_metrics(
        [
            score_case(_outcome()),
            score_case(
                _outcome(
                    case_id="eval_005",
                    category="permission",
                    status="NOT_RUN_IDENTITY_MISMATCH",
                    expected_status="REJECTED",
                    result_ok=False,
                    error_code="EVAL_IDENTITY_TOKEN_REQUIRED",
                    last_action="FAIL",
                    sql_execution_accurate=False,
                )
            ),
        ]
    )
    tcr = next(item for item in metrics if item["name"] == "task_completion_rate")
    assert tcr["denominator"] == 1
    assert tcr["numerator"] == 1


def test_production_ablations_use_http_outcomes_not_test_doubles():
    from backend.app.evaluation.ablations import production_ablations

    outcomes = [
        score_case(_outcome()),
        score_case(
            _outcome(
                case_id="eval_fail",
                status="FAILED",
                expected_status="SUCCEEDED",
                result_ok=False,
                sql_execution_accurate=True,
                grounded_context_tokens=400,
            )
        ),
    ]
    report = production_ablations(outcomes, full_schema_tokens=80_000)
    assert report["non_production"] is False
    contrast = report["sql_execution_accuracy_vs_task_completion_rate"]
    assert contrast["sql_execution_accuracy"]["value"] != contrast["task_completion_rate"]["value"]
    tokens = report["full_schema_injection_vs_bounded_context"]
    assert tokens["bounded_context_tokens"] < tokens["full_schema_estimated_tokens"]


def test_failure_improvements_log_covers_five_to_eight_cases():
    log = json.loads((ROOT / "reports" / "failure-improvements.json").read_text(encoding="utf-8"))
    cases = log["cases"]
    assert 5 <= len(cases) <= 8
    for item in cases:
        assert item["case_id"]
        assert item["baseline_error"]
        assert item["change"]
        assert item["reproduce_command"]
        assert "after_production_run" in item
