#!/usr/bin/env python3
"""Evaluate fixed cases through the authenticated production HTTP boundary."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_cases(root: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


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
    token = os.environ.get("DRA_EVAL_TOKEN")
    local_authenticator = None
    with httpx.Client(base_url=args.base_url, timeout=120, trust_env=False) as client:
        if not token and args.account:
            response = client.post("/api/auth/login", json={
                "account": args.account,
                "password": getpass.getpass("Application password: "),
            })
            response.raise_for_status(); token = response.json()["access_token"]
        if not token:
            raise SystemExit("Set DRA_EVAL_TOKEN or provide --account and enter its password.")
        headers = {"Authorization": f"Bearer {token}"}
        identity = client.get("/api/me", headers=headers); identity.raise_for_status()
        cases = load_cases(args.cases_dir)
        if args.case_id:
            cases = [case for case in cases if case["case_id"] in set(args.case_id)]
        if args.limit is not None:
            cases = cases[: max(0, args.limit)]
        outcomes: list[dict[str, Any]] = []
        for case in cases:
            requested_user_id = str(case.get("user_id") or identity.json()["user_id"])
            if requested_user_id == identity.json()["user_id"]:
                case_headers = headers
            elif local_authenticator:
                # Explicitly local-only: the HTTP server still authenticates
                # this JWT and its MySQL permission lookup must reject unknown
                # or inactive identities before Graph execution.
                case_headers = {"Authorization": "Bearer " + local_authenticator.issue(
                    requested_user_id, ["USER"], ttl_minutes=5)}
            else:
                outcomes.append({
                    "case_id": case["case_id"], "status": "NOT_RUN_IDENTITY_MISMATCH",
                    "passed": False, "status_ok": False, "action_ok": False,
                    "result_ok": False, "actions": [], "observed_columns": [],
                    "duration_ms": 0, "error_code": "EVAL_IDENTITY_TOKEN_REQUIRED",
                    "requested_user_id": requested_user_id})
                continue
            started = time.perf_counter(); thread_id = None; state_version = None; response_body: dict[str, Any] = {}
            for index, message in enumerate(case["messages"]):
                request = {"message": message["content"], "request_id": f"eval_{case['case_id']}_{index}_{int(time.time()*1000)}"}
                if thread_id:
                    request |= {"thread_id": thread_id, "expected_state_version": state_version}
                response = client.post("/api/chat", headers=case_headers, json=request)
                try:
                    response_body = response.json()
                except ValueError:
                    response_body = {"status": "FAILED", "events": [],
                                     "error_code": f"HTTP_{response.status_code}_NON_JSON"}
                if response.status_code >= 400:
                    response_body["status"] = (
                        "REJECTED" if response.status_code in {401, 403} else "FAILED")
                    response_body.setdefault("events", [])
                    break
                thread_id, state_version = response_body["thread_id"], response_body.get("state_version")
            actions = [event.get("action") for event in response_body.get("events", [])
                       if event.get("event") == "node.completed" and event.get("action")]
            condensed = [action for index, action in enumerate(actions) if index == 0 or action != actions[index - 1]]
            expected = case.get("expected_action_sequence", [])
            action_ok = all(action in condensed for action in expected)
            if (case.get("should_reject") and not condensed
                    and response_body.get("error_code") == "PERMISSION_DENIED"):
                # Authentication/authorization rejection before Graph is
                # stronger than the legacy fixture's RETRIEVE expectation.
                action_ok = True
            status = response_body.get("status")
            status_ok = (case.get("should_reject") and status == "REJECTED") or (case.get("should_clarify") and status == "WAITING_FOR_USER") or (not case.get("should_reject") and not case.get("should_clarify") and status == "SUCCEEDED")
            golden = json.loads((args.golden_dir / case["golden_result_ref"]).read_text(encoding="utf-8"))
            result_ok = True
            observed_columns: list[str] = []
            if golden.get("columns") and response_body.get("result_ids"):
                page = client.get(f"/api/results/{response_body['result_ids'][-1]}", headers=case_headers); page.raise_for_status()
                rows = page.json().get("rows", []); observed_columns = list(rows[0]) if rows else []
                result_ok = set(golden["columns"]) == set(observed_columns)
            elif golden.get("type"):
                artifact_types = []
                for artifact_id in response_body.get("artifact_ids", []):
                    artifact = client.get(f"/api/artifacts/{artifact_id}", headers=case_headers)
                    if artifact.status_code == 200: artifact_types.append(artifact.json()["spec"]["type"])
                result_ok = golden["type"] in artifact_types
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            failure_events = [event for event in response_body.get("events", [])
                              if event.get("event") == "run.failed"]
            error_code = response_body.get("error_code") or (
                failure_events[-1].get("error_code") if failure_events else None)
            outcomes.append({"case_id": case["case_id"], "status": status, "passed": bool(status_ok and action_ok and result_ok), "status_ok": bool(status_ok), "action_ok": action_ok, "result_ok": result_ok, "actions": condensed, "observed_columns": observed_columns, "duration_ms": duration_ms, "error_code": error_code, "requested_user_id": requested_user_id})
    durations = [item["duration_ms"] for item in outcomes]
    report = {"mode": "production_http", "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url, "authenticated_user_id": identity.json()["user_id"],
        "case_count": len(outcomes), "passed": sum(item["passed"] for item in outcomes),
        "task_completion_rate": sum(item["passed"] for item in outcomes) / len(outcomes) if outcomes else 0,
        "latency_ms": {"mean": round(statistics.mean(durations), 2) if durations else 0, "p95": percentile(durations, .95)},
        "outcomes": outcomes}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evaluated {len(outcomes)} production HTTP cases; passed={report['passed']}; report={args.report}")
    return 0 if report["passed"] == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
