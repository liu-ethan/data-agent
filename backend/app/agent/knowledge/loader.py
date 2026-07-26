from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_DIR = Path(__file__).resolve().parent


class KnowledgeConfigError(ValueError):
    pass


def _knowledge_dir() -> Path:
    override = os.environ.get("APP_KNOWLEDGE_DIR")
    if override:
        return Path(override)
    return _DEFAULT_DIR


def clear_cache() -> None:
    _load_metrics.cache_clear()


def _require_str(value: Any, field: str, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeConfigError(f"Metric {key} missing non-empty {field}")
    return value.strip()


def _require_str_list(value: Any, field: str, key: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise KnowledgeConfigError(f"Metric {key} missing non-empty {field}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise KnowledgeConfigError(f"Metric {key} has invalid {field}")
        text = item.strip()
        if text not in out:
            out.append(text)
    return out


def _normalize_metric(key: str, raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise KnowledgeConfigError(f"Metric {key} must be a mapping")
    display_name = _require_str(raw.get("display_name"), "display_name", key)
    expression = _require_str(raw.get("expression"), "expression", key)
    tables = _require_str_list(raw.get("tables"), "tables", key)
    notes = _require_str(raw.get("notes"), "notes", key)
    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        raise KnowledgeConfigError(f"Metric {key} aliases must be a list")
    aliases: list[str] = []
    for alias in [display_name, *aliases_raw, key]:
        if not isinstance(alias, str) or not alias.strip():
            raise KnowledgeConfigError(f"Metric {key} has invalid alias")
        text = alias.strip()
        if text not in aliases:
            aliases.append(text)
    return {
        "key": key,
        "display_name": display_name,
        "aliases": aliases,
        "expression": expression,
        "tables": tables,
        "notes": notes,
    }


@lru_cache(maxsize=8)
def _load_metrics(dir_key: str) -> dict[str, dict]:
    path = Path(dir_key) / "metrics.yaml"
    if not path.is_file():
        raise KnowledgeConfigError(f"Metric knowledge file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise KnowledgeConfigError(f"Metric knowledge root must be a mapping: {path}")
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise KnowledgeConfigError(f"Metric knowledge missing metrics: {path}")

    normalized: dict[str, dict] = {}
    for key, spec in metrics.items():
        if not isinstance(key, str) or not key.strip():
            raise KnowledgeConfigError("Metric key must be a non-empty string")
        normalized[key.strip()] = _normalize_metric(key.strip(), spec)
    return normalized


def load_metrics() -> dict[str, dict]:
    return _load_metrics(str(_knowledge_dir()))
