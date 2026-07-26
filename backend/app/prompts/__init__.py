from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

_DEFAULT_DIR = Path(__file__).resolve().parent


class PromptRenderError(ValueError):
    pass


def _prompts_dir() -> Path:
    override = os.environ.get("APP_PROMPTS_DIR")
    if override:
        return Path(override)
    return _DEFAULT_DIR


def clear_cache() -> None:
    _load_raw.cache_clear()


def _field_names(template: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if not field_name:
            continue
        names.add(field_name.split(".")[0].split("[")[0])
    return names


@lru_cache(maxsize=32)
def _load_raw(name: str, dir_key: str) -> dict[str, str]:
    path = Path(dir_key) / f"{name}.yaml"
    if not path.is_file():
        raise PromptRenderError(f"Prompt file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise PromptRenderError(f"Prompt root must be a mapping: {path}")
    system = data.get("system")
    user = data.get("user")
    if not isinstance(system, str) or not system.strip():
        raise PromptRenderError(f"Prompt missing non-empty system: {path}")
    if not isinstance(user, str) or not user.strip():
        raise PromptRenderError(f"Prompt missing non-empty user: {path}")
    return {"system": system, "user": user}


def render(name: str, /, **variables: Any) -> dict[str, str]:
    """Load prompts/<name>.yaml, format system/user, return {"system","user"}.

    ``name`` is positional-only so templates may use a ``{name}`` variable.
    """
    raw = _load_raw(name, str(_prompts_dir()))
    required = _field_names(raw["system"]) | _field_names(raw["user"])
    provided = set(variables)
    missing = required - provided
    if missing:
        raise PromptRenderError(
            f"Missing prompt variables for {name}: {sorted(missing)}"
        )
    unexpected = provided - required
    if unexpected:
        raise PromptRenderError(
            f"Unexpected prompt variables for {name}: {sorted(unexpected)}"
        )
    try:
        return {
            "system": raw["system"].format_map(variables),
            "user": raw["user"].format_map(variables),
        }
    except (KeyError, ValueError) as exc:
        raise PromptRenderError(f"Failed to render prompt {name}: {exc}") from exc
