#!/usr/bin/env python3
"""Evaluate fixed cases through the authenticated production HTTP boundary."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings
from backend.app.evaluation import (
    compare_results,
    load_cases,
    production_ablations,
    run_security_probe,
    score_case,
    summarize_metrics,
    write_report,
)
from backend.app.evaluation.observe import evidence_from_payload
from backend.app.evaluation.reproducibility import build_reproducibility
from backend.app.evaluation.scoring import CaseOutcome


def _expected_status(case) -> str:
    if case.should_clarify:
        return "WAITING_FOR_USER"
    if case.should_reject:
        return "REJECTED"
    return "SUCCEEDED"


def _actions(events: list[dict[str, Any]]) -> list[str]:
    actions = [
        str(event.get("action"))
        for event in events
        if event.get("event") in {"node.started", "node.completed"} and event.get("action")
    ]
    condensed: list[str] = []
    for action in actions:
        if action and action != "None" and (not condensed or condensed[-1] != action):
            condensed.append(action)
    return condensed


def _usage(events: list[dict[str, Any]]) -> tuple[int, int]:
    input_tokens = output_tokens = 0
    for event in events:
        usage = event.get("model_usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return input_tokens, output_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases-dir", type=Path, default=Path("tests/eval_cases"))
    parser.add_argument("--golden-dir", type=Path, default=Path("tests/golden_results"))
    parser.add_argument("--report", type=Path, default=Path("reports/production-evaluation.json"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--account")
    args = parser.parse_args()
    settings = load_settings()
    token = os.environ.get("DRA_EVAL_TOKEN")
    with httpx.Client(base_url=args.base_url, timeout=120, trust_env=False) as client:
        if not token and args.account:
            response = client.post(
                "/api/auth/login",
                json={
                    "account": args.account,
                    "password": getpass.getpass("Application password: "),
                },
            )
            response.raise_for_status()
            token = response.json()["access_token"]
        if not token:
            raise SystemExit("Set DRA_EVAL_TOKEN or provide --account and enter its password.")
        headers = {"Authorization": f"Bearer {token}"}
        identity = client.get("/api/me", headers=headers)
        identity.raise_for_status()
        authenticated_user = identity.json()["user_id"]
        cases = load_cases(args.cases_dir)
        if args.case_id:
            cases = [case for case in cases if case.case_id in set(args.case_id)]
        if args.limit is not None:
            cases = cases[: max(0, args.limit)]
        outcomes: list[CaseOutcome] = []
        for case in cases:
            expected = _expected_status(case)
            if case.deferred_reason:
                outcomes.append(
                    score_case(
                        CaseOutcome(
                            case_id=case.case_id,
                            category=case.category,
                            status="NOT_RUN",
                            expected_status=expected,
                            action_sequence=[],
                            expected_action_sequence=list(case.expected_action_sequence),
                            required_objects=list(case.required_objects),
                            required_fields=list(case.required_fields),
                            deferred=True,
                            data_version=case.data_version,
                            catalog_version=case.catalog_version,
                        )
                    )
                )
                continue
            if case.user_id != authenticated_user:
                outcomes.append(
                    score_case(
                        CaseOutcome(
                            case_id=case.case_id,
                            category=case.category,
                            status="NOT_RUN_IDENTITY_MISMATCH",
                            expected_status=expected,
                            action_sequence=[],
                            expected_action_sequence=list(case.expected_action_sequence),
                            required_objects=list(case.required_objects),
                            required_fields=list(case.required_fields),
                            error_code="EVAL_IDENTITY_TOKEN_REQUIRED",
                            last_action="FAIL",
                            data_version=case.data_version,
                            catalog_version=case.catalog_version,
                        )
                    )
                )
                continue
            started = time.perf_counter()
            thread_id = None
            state_version = None
            body: dict[str, Any] = {}
            for index, message in enumerate(case.messages):
                for attempt in range(3):
                    if (
                        body.get("status") == "WAITING_FOR_USER"
                        and body.get("interrupt")
                        and thread_id
                    ):
                        interrupt = body["interrupt"]
                        response = client.post(
                            f"/api/threads/{thread_id}/interrupts/{interrupt['interrupt_id']}/resume",
                            headers=headers,
                            json={
                                "answer": message.content,
                                "client_request_id": f"eval_{case.case_id}_{index}_{int(time.time() * 1000)}",
                                "expected_state_version": state_version,
                            },
                        )
                    else:
                        payload = {
                            "message": message.content,
                            "request_id": f"eval_{case.case_id}_{index}_{int(time.time() * 1000)}",
                        }
                        if thread_id:
                            payload["thread_id"] = thread_id
                            payload["expected_state_version"] = state_version
                        response = client.post("/api/chat", headers=headers, json=payload)
                    try:
                        body = response.json()
                    except ValueError:
                        body = {
                            "status": "FAILED",
                            "events": [],
                            "error_code": f"HTTP_{response.status_code}_NON_JSON",
                        }
                    if body.get("error_code") == "LLM_RATE_LIMITED" and attempt < 2:
                        time.sleep(2 * (attempt + 1))
                        continue
                    break
                if response.status_code >= 400:
                    body["status"] = "REJECTED" if response.status_code in {401, 403} else "FAILED"
                    body.setdefault("events", [])
                    break
                thread_id = body.get("thread_id") or thread_id
                state_version = body.get("state_version")
            latency = round((time.perf_counter() - started) * 1000, 2)
            events = list(body.get("events") or [])
            actions = _actions(events)
            golden = json.loads(
                (args.golden_dir / case.golden_result_ref).read_text(encoding="utf-8")
            )
            columns: list[str] = []
            rows: list[dict[str, Any]] = []
            artifact_types: list[str] = []
            if body.get("result_ids"):
                page = client.get(f"/api/results/{body['result_ids'][-1]}", headers=headers)
                if page.status_code == 200:
                    rows = list(page.json().get("rows") or [])
                    columns = list(rows[0]) if rows else []
            for artifact_id in body.get("artifact_ids") or []:
                artifact = client.get(f"/api/artifacts/{artifact_id}", headers=headers)
                if artifact.status_code == 200:
                    artifact_types.append(artifact.json()["spec"]["type"])
            result_ok = compare_results(
                golden,
                observed_columns=columns,
                observed_rows=rows,
                spec=case.result_compare,
                artifact_types=artifact_types,
            )
            if case.should_reject:
                result_ok = body.get("status") == "REJECTED"
            elif golden.get("status"):
                result_ok = body.get("status") == golden["status"]
            sql_ok = (not golden.get("columns")) or set(golden.get("columns") or []) == set(columns)
            failure_events = [event for event in events if event.get("event") == "run.failed"]
            error_code = body.get("error_code") or (
                failure_events[-1].get("error_code") if failure_events else None
            )
            input_tokens, output_tokens = _usage(events)
            seen = evidence_from_payload(body)
            outcomes.append(
                score_case(
                    CaseOutcome(
                        case_id=case.case_id,
                        category=case.category,
                        status=str(body.get("status") or "FAILED"),
                        expected_status=expected,
                        action_sequence=actions,
                        expected_action_sequence=list(case.expected_action_sequence),
                        observed_intent=seen.get("intent"),
                        expected_intent=case.golden_task_frame.intent.value,
                        observed_metric_ids=list(seen.get("metric_ids") or []),
                        expected_metric_ids=list(case.golden_task_frame.metric_ids),
                        observed_objects=list(seen.get("objects") or []),
                        required_objects=list(case.required_objects),
                        observed_fields=list(seen.get("fields") or []),
                        required_fields=list(case.required_fields),
                        observed_columns=columns,
                        observed_rows=rows,
                        artifact_types=artifact_types,
                        coverage=seen.get("coverage"),
                        schema_gap_recovered=seen.get("schema_gap_recovered"),
                        retrieval_rounds=int(seen.get("retrieval_rounds") or 0),
                        graph_steps=len(actions),
                        grounded_context_tokens=seen.get("grounded_tokens"),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency,
                        error_code=error_code,
                        last_action=actions[-1] if actions else str(body.get("status")),
                        result_ok=result_ok,
                        sql_execution_accurate=sql_ok,
                        data_version=case.data_version,
                        catalog_version=case.catalog_version,
                    )
                )
            )
    metrics = summarize_metrics(
        outcomes, filter_note="production HTTP runnable cases; deferred spec 06 HITL excluded"
    )
    failures = [
        item.failure_record() for item in outcomes if not item.deferred and not item.completed
    ]
    for item in failures:
        item["reproduce_command"] = (
            "python3.12 scripts/run_production_evaluation.py "
            f"--account {args.account or authenticated_user} --case-id {item['case_id']}"
        )
    report = {
        "evaluation_mode": "production_runtime",
        "non_production": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "authenticated_user_id": authenticated_user,
        "case_count": len(outcomes),
        "metrics": metrics,
        "security_probe": run_security_probe(),
        "ablations": production_ablations(outcomes),
        "cases": [item.as_row() for item in outcomes],
        "failure_cases": failures[:8],
        "trace_samples": [
            {"case_id": item.case_id, "action_sequence": item.action_sequence}
            for item in outcomes
            if not item.deferred
        ][:5],
        "reproducibility": build_reproducibility(
            command="python3.12 scripts/run_production_evaluation.py --account u_demo_user",
            execution_mode="production_runtime",
            data_version="seed_v1",
            catalog_version="catalog_v1",
            settings=settings.raw,
        ),
    }
    tcr = next(item for item in metrics if item["name"] == "task_completion_rate")
    report["task_completion_rate"] = tcr["value"]
    report["passed"] = tcr["numerator"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, args.report.parent, stem=args.report.stem)
    print(
        f"evaluated {len(outcomes)} production HTTP cases; "
        f"passed={report['passed']}; report={args.report}"
    )
    runnable = [item for item in outcomes if not item.deferred]
    return (
        0
        if runnable
        and all(item.completed or item.status.startswith("NOT_RUN") for item in runnable)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
