from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.log.logging import log_event


def audit_log_path() -> Path:
    return get_settings().audit_log_file


def append_audit(record: dict) -> None:
    try:
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = dict(record)
        if "ts" not in row:
            row["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log_event("WARNING", "audit_write_failed", detail={"error": str(exc)[:200]})
