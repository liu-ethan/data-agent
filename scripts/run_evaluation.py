#!/usr/bin/env python3
"""Run fixed cases without printing configuration or secret values."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings
from backend.app.evaluation import load_cases, run_cases, write_report
from backend.app.testing import build_test_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-test-double", action="store_true", help="run the SQLite/rule-based compatibility harness; results are explicitly non-production")
    args = parser.parse_args()
    if not args.allow_test_double:
        raise SystemExit("Refusing to create a production-looking report from the deterministic test double. Use --allow-test-double for compatibility regression only.")
    settings = load_settings()
    cases = load_cases(settings.raw.get("evaluation", {}).get("cases_dir", "tests/eval_cases"))
    report = run_cases(cases, build_test_runtime(settings=settings.raw))
    paths = write_report(report, settings.raw.get("evaluation", {}).get("reports_dir", "reports"))
    print(f"evaluated {report['case_count']} compatibility cases; test_double_task_completion_rate={report['test_double_task_completion_rate']:.3f}")
    print("reports:", ", ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
