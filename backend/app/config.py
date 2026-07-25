from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseModel):
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    jwt_secret: str = "change-me"
    admin_invite_code: str = "your-invite-code"
    database_path: str = "data/ecommerce.db"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    frontend_port: int = 5173
    frontend_api_base_url: str = "http://127.0.0.1:8000"

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path


def default_config_path() -> Path:
    override = os.environ.get("APP_CONFIG")
    if override:
        return Path(override)
    return REPO_ROOT / "config.yaml"


def _require_mapping(data: Any, key: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"config.{key} must be a mapping")
    return value


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or default_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing config file: {path}. Copy config_template.yaml to config.yaml."
        )
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    llm = _require_mapping(raw, "llm")
    backend = _require_mapping(raw, "backend")
    frontend = _require_mapping(raw, "frontend")

    return Settings(
        openai_api_key=str(llm.get("api_key") or ""),
        openai_base_url=str(llm.get("base_url") or ""),
        openai_model=str(llm.get("model") or ""),
        jwt_secret=str(backend.get("jwt_secret") or "change-me"),
        admin_invite_code=str(backend.get("admin_invite_code") or "your-invite-code"),
        database_path=str(backend.get("database_path") or "data/ecommerce.db"),
        backend_host=str(backend.get("host") or "0.0.0.0"),
        backend_port=int(backend.get("port") or 8000),
        cors_origins=list(
            backend.get("cors_origins")
            or [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        ),
        frontend_port=int(frontend.get("port") or 5173),
        frontend_api_base_url=str(
            frontend.get("api_base_url") or "http://127.0.0.1:8000"
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()
