from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.app.resources.paths import seeds_dir


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_domain() -> dict[str, Any]:
    data = _load_yaml(seeds_dir() / "domain.yaml")
    if not isinstance(data, dict):
        raise TypeError("domain.yaml must be a mapping")
    return data


@lru_cache(maxsize=1)
def load_slice() -> dict[str, Any]:
    data = _load_yaml(seeds_dir() / "slice.yaml")
    if not isinstance(data, dict):
        raise TypeError("slice.yaml must be a mapping")
    return data


@lru_cache(maxsize=1)
def load_metrics() -> list[dict[str, Any]]:
    data = _load_yaml(seeds_dir() / "metrics.yaml")
    if not isinstance(data, list):
        raise TypeError("metrics.yaml must be a list")
    return data


@lru_cache(maxsize=1)
def load_write_ops_raw() -> list[dict[str, Any]]:
    data = _load_yaml(seeds_dir() / "write_ops.yaml")
    if not isinstance(data, list):
        raise TypeError("write_ops.yaml must be a list")
    return data


def tenant_id() -> str:
    return str(load_domain()["tenant_id"])


def mysql_database() -> str:
    return str(load_domain()["mysql_database"])


def empty_thread_title() -> str:
    return str(load_domain()["empty_thread_title"])


def title_max_chars() -> int:
    return int(load_domain()["title_max_chars"])


def greeting() -> str:
    return str(load_domain()["greeting"])


def suggested_questions() -> list[str]:
    return [str(item) for item in load_domain()["suggested_questions"]]


def role_labels() -> dict[str, str]:
    raw = load_domain()["role_labels"]
    return {str(k): str(v) for k, v in raw.items()}


def paid_statuses() -> list[str]:
    return [str(item) for item in load_domain()["paid_statuses"]]


def sku_status_values() -> frozenset[str]:
    return frozenset(str(item) for item in load_domain()["sku_status_values"])


def write_max_affected_rows() -> int:
    return int(load_domain()["write_max_affected_rows"])


def query_max_repairs() -> int:
    return int(load_domain()["query_max_repairs"])


def result_preview_rows() -> int:
    return int(load_domain()["result_preview_rows"])


def sku_search_limit() -> int:
    return int(load_domain()["sku_search_limit"])


def pbkdf2_iterations() -> int:
    return int(load_domain()["pbkdf2_iterations"])


def jwt_algorithm() -> str:
    return str(load_domain()["jwt_algorithm"])


def time_presets() -> dict[str, str]:
    raw = load_domain()["time_presets"]
    return {str(k).lower(): str(v) for k, v in raw.items()}


def empty_text() -> frozenset[str]:
    return frozenset(str(item) for item in load_domain()["empty_text"])


def dimension_aliases() -> dict[str, str]:
    raw = load_domain().get("dimension_aliases") or {}
    return {str(key): str(value) for key, value in raw.items()}


def control_tables() -> tuple[str, ...]:
    return tuple(str(item) for item in load_domain()["control_tables"])


def writable_tables() -> frozenset[str]:
    return frozenset(str(item) for item in load_domain()["writable_tables"])


def slice_tables() -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for item in load_slice()["tables"]:
        out.append(
            (
                str(item["name"]),
                str(item["business_name"]),
                str(item["domain"]),
                str(item["grain"]),
            )
        )
    return out


def relations() -> list[tuple[str, str, str, str, str, str]]:
    out: list[tuple[str, str, str, str, str, str]] = []
    for item in load_slice()["relations"]:
        out.append(
            (
                str(item["left"]),
                str(item["left_col"]),
                str(item["right"]),
                str(item["right_col"]),
                "many_to_one",
                "fk",
            )
        )
    return out


def business_tables() -> frozenset[str]:
    return frozenset(name for name, *_ in slice_tables())


def all_tables() -> list[str]:
    return [name for name, *_ in slice_tables()]


def all_metrics() -> list[str]:
    return [str(item["metric_id"]) for item in load_metrics()]


def allowed_operation_types() -> frozenset[str]:
    return frozenset(str(item["operation_type"]) for item in load_write_ops_raw())


def think_steps() -> dict[str, dict[str, str]]:
    raw = load_domain().get("think_steps") or {}
    out: dict[str, dict[str, str]] = {}
    for node, item in raw.items():
        if not isinstance(item, dict):
            continue
        out[str(node)] = {"label": str(item["label"]), "text": str(item["text"])}
    return out


def login_meta() -> dict[str, Any]:
    raw = load_domain().get("login") or {}
    capabilities = []
    for item in raw.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        capabilities.append({"title": str(item["title"]), "body": str(item["body"])})
    return {
        "eyebrow": str(raw.get("eyebrow") or "电商问数"),
        "headline": str(raw.get("headline") or "用一句话问经营数字"),
        "lead": str(raw.get("lead") or ""),
        "ticker_caption": str(raw.get("ticker_caption") or "可问指标"),
        "ticker": [{"label": str(item["name"])} for item in load_metrics()],
        "capabilities": capabilities,
    }


def ui_meta() -> dict[str, Any]:
    return {
        "greeting": greeting(),
        "suggested_questions": suggested_questions(),
        "empty_thread_title": empty_thread_title(),
        "role_labels": role_labels(),
        "login": login_meta(),
    }


# Compatibility aliases used by scripts and tests.
TENANT_ID = tenant_id()
SLICE_TABLES = slice_tables()
RELATIONS = relations()
METRICS = load_metrics()
ALL_TABLES = all_tables()
ALL_METRICS = all_metrics()
BUSINESS_TABLES = business_tables()
