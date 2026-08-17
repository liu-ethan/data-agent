"""Reproducibility block required by spec 07 reports."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from .cases import TIME_ANCHOR


def code_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_reproducibility(
    *,
    command: str,
    execution_mode: str,
    data_version: str,
    catalog_version: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = settings or {}
    llm = raw.get("llm", {})
    runtime = raw.get("runtime_agent", {})
    embedding = llm.get("embedding", {})
    return {
        "command": command,
        "execution_mode": execution_mode,
        "code_version": code_version(),
        "python_version": sys.version.split()[0],
        "data_version": data_version,
        "catalog_version": catalog_version,
        "index_version": raw.get("milvus", {}).get("index_version"),
        "prompt_version": "task_understanding_v1+query_draft_v1+answer_draft_v1",
        "tokenizer_version": "cl100k_base_estimate_v1",
        "timezone": raw.get("app", {}).get("timezone", "Asia/Shanghai"),
        "time_anchor": TIME_ANCHOR,
        "random_seed": 0,
        "budgets": {
            "max_iterations": runtime.get("max_iterations", 6),
            "max_retrieval_rounds": runtime.get("max_retrieval_rounds", 2),
            "max_query_retries": runtime.get("max_query_retries", 1),
        },
        "llm": {
            "provider": llm.get("provider", "unconfigured"),
            "protocol": llm.get("protocol"),
            "model": llm.get("model") or llm.get("chat_model"),
        },
        "embedding": {
            "provider": embedding.get("provider"),
            "model": embedding.get("model"),
        },
    }
