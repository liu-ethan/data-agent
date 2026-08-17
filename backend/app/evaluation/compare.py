"""Result snapshot comparison used by Task Completion and Result Accuracy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from .cases import ResultCompare


def compare_results(
    golden: Mapping[str, Any],
    *,
    observed_columns: Sequence[str],
    observed_rows: Sequence[Mapping[str, Any]],
    spec: ResultCompare | Mapping[str, Any],
    artifact_types: Sequence[str] | None = None,
) -> bool:
    compare = spec if isinstance(spec, ResultCompare) else ResultCompare.model_validate(spec)
    if golden.get("type"):
        return str(golden["type"]) in set(artifact_types or ())
    if "columns" not in golden:
        return True
    if set(golden["columns"]) != set(observed_columns):
        return False
    expected_rows = golden.get("rows")
    if expected_rows is None:
        return True
    if len(expected_rows) != len(observed_rows):
        return False
    columns = list(golden["columns"])
    if compare.row_order == "explicit":
        return all(
            _row_equal(expected, observed, columns, compare)
            for expected, observed in zip(expected_rows, observed_rows, strict=True)
        )
    unmatched = [dict(row) for row in observed_rows]
    for expected in expected_rows:
        index = next(
            (
                i
                for i, observed in enumerate(unmatched)
                if _row_equal(expected, observed, columns, compare)
            ),
            None,
        )
        if index is None:
            return False
        unmatched.pop(index)
    return not unmatched


def _row_equal(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
    columns: Sequence[str],
    spec: ResultCompare,
) -> bool:
    return all(
        _values_equal(expected.get(column), observed.get(column), spec) for column in columns
    )


def _values_equal(expected: Any, observed: Any, spec: ResultCompare) -> bool:
    if spec.null_equals_zero:
        expected = 0 if expected is None else expected
        observed = 0 if observed is None else observed
    if expected is None or observed is None:
        return expected is None and observed is None
    if _is_number(expected) and _is_number(observed):
        delta = abs(float(expected) - float(observed))
        if delta <= spec.numeric_abs_tolerance:
            return True
        scale = max(abs(float(expected)), abs(float(observed)), 1e-12)
        return delta <= spec.numeric_rel_tolerance * scale
    return expected == observed


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))
    )
