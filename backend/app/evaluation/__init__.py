"""Reproducible task-level evaluation owned by spec 07."""

from .ablations import production_ablations, run_ablations
from .cases import (
    DEFAULT_CATALOG_VERSION,
    DEFAULT_DATA_VERSION,
    TIME_ANCHOR,
    EvalBudgets,
    EvalCase,
    GoldenTaskFrame,
    ResultCompare,
    load_cases,
)
from .compare import compare_results
from .harness import run_cases
from .metrics import metric_definitions, summarize_metrics
from .observe import evidence_from_payload
from .report import write_report
from .reproducibility import build_reproducibility, code_version
from .scoring import CaseOutcome, score_case
from .security import run_security_probe

__all__ = [
    "DEFAULT_CATALOG_VERSION",
    "DEFAULT_DATA_VERSION",
    "TIME_ANCHOR",
    "CaseOutcome",
    "EvalBudgets",
    "EvalCase",
    "GoldenTaskFrame",
    "ResultCompare",
    "build_reproducibility",
    "code_version",
    "compare_results",
    "evidence_from_payload",
    "load_cases",
    "metric_definitions",
    "production_ablations",
    "run_ablations",
    "run_cases",
    "run_security_probe",
    "score_case",
    "summarize_metrics",
    "write_report",
]
