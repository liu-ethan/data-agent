"""JSON/CSV report writer. Reports never embed secrets or raw prompts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_report(
    report: dict[str, Any],
    directory: str | Path,
    *,
    stem: str = "evaluation",
) -> tuple[Path, Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"{stem}.json"
    csv_path = output / f"{stem}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = report.get("cases", [])
    fieldnames = ["case_id", "category", "status", "expected_status", "passed", "latency_ms"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return json_path, csv_path
