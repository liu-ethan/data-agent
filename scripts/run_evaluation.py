#!/usr/bin/env python3
"""Run fixed cases without printing configuration or secret values."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings
from backend.app.evaluation import load_cases, run_cases, write_report
from backend.app.testing import build_test_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-test-double",
        action="store_true",
        help="run the SQLite/rule-based compatibility harness; results are non-production",
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--cases-dir", type=Path)
    parser.add_argument("--golden-dir", type=Path, default=Path("tests/golden_results"))
    parser.add_argument("--reports-dir", type=Path)
    args = parser.parse_args()
    if not args.allow_test_double:
        raise SystemExit(
            "Refusing to create a production-looking report from the deterministic "
            "test double. Use --allow-test-double for compatibility regression only."
        )
    settings = load_settings()
    evaluation = settings.raw.get("evaluation", {})
    cases_dir = args.cases_dir or Path(evaluation.get("cases_dir", "tests/eval_cases"))
    reports_dir = args.reports_dir or Path(evaluation.get("reports_dir", "reports"))
    cases = load_cases(cases_dir)
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case.case_id in wanted]
        if not cases:
            raise SystemExit(f"no eval cases matched {sorted(wanted)}")
    report = run_cases(
        cases,
        build_test_runtime(settings=settings.raw),
        golden_dir=args.golden_dir,
        settings=settings.raw,
    )
    paths = write_report(report, reports_dir)
    print(
        f"evaluated {report['case_count']} compatibility cases; "
        f"test_double_task_completion_rate={report['test_double_task_completion_rate']:.3f}"
    )
    print("reports:", ", ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
