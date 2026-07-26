from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

_DEFAULT_CORS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class Settings(BaseModel):
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    llm_temperature: float = 0.0
    jwt_secret: str = "change-me"
    jwt_expire_days: int = 7
    admin_invite_code: str = "your-invite-code"
    database_path: str = "data/ecommerce.db"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS))
    frontend_host: str = "0.0.0.0"
    frontend_port: int = 5173
    frontend_api_base_url: str = ""
    frontend_tables_page_size: int = 50

    sandbox_max_select_rows: int = 100
    sandbox_max_write_rows: int = 100
    sandbox_connect_timeout_s: float = 5.0

    memory_max_turns_per_session: int = 10
    memory_max_summaries_per_user: int = 20
    memory_recent_summaries_limit: int = 5
    memory_session_title_max_chars: int = 10

    agent_react_max_steps: int = 5

    logging_dir: str = "logs"
    logging_max_bytes: int = 10 * 1024 * 1024
    audit_path: str = "logs/audit.jsonl"

    tables_page_size: int = 50
    display_max_rows: int = 100

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path

    @property
    def logging_dir_path(self) -> Path:
        path = Path(self.logging_dir)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    @property
    def audit_log_file(self) -> Path:
        path = Path(self.audit_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
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


def _int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


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
    sandbox = _require_mapping(backend, "sandbox")
    memory = _require_mapping(backend, "memory")
    agent = _require_mapping(backend, "agent")
    logging_cfg = _require_mapping(backend, "logging")

    api_base = frontend.get("api_base_url")
    if api_base is None:
        api_base = ""

    return Settings(
        openai_api_key=str(llm.get("api_key") or ""),
        openai_base_url=str(llm.get("base_url") or ""),
        openai_model=str(llm.get("model") or ""),
        llm_temperature=_float(llm.get("temperature"), 0.0),
        jwt_secret=str(backend.get("jwt_secret") or "change-me"),
        jwt_expire_days=_int(backend.get("jwt_expire_days"), 7),
        admin_invite_code=str(backend.get("admin_invite_code") or "your-invite-code"),
        database_path=str(backend.get("database_path") or "data/ecommerce.db"),
        backend_host=str(backend.get("host") or "0.0.0.0"),
        backend_port=_int(backend.get("port"), 8000),
        cors_origins=list(backend.get("cors_origins") or list(_DEFAULT_CORS)),
        frontend_host=str(frontend.get("host") or "0.0.0.0"),
        frontend_port=_int(frontend.get("port"), 5173),
        frontend_api_base_url=str(api_base),
        frontend_tables_page_size=_int(frontend.get("tables_page_size"), 50),
        sandbox_max_select_rows=_int(sandbox.get("max_select_rows"), 100),
        sandbox_max_write_rows=_int(sandbox.get("max_write_rows"), 100),
        sandbox_connect_timeout_s=_float(sandbox.get("connect_timeout_s"), 5.0),
        memory_max_turns_per_session=_int(memory.get("max_turns_per_session"), 10),
        memory_max_summaries_per_user=_int(memory.get("max_summaries_per_user"), 20),
        memory_recent_summaries_limit=_int(memory.get("recent_summaries_limit"), 5),
        memory_session_title_max_chars=_int(memory.get("session_title_max_chars"), 10),
        agent_react_max_steps=_int(agent.get("react_max_steps"), 5),
        logging_dir=str(logging_cfg.get("dir") or "logs"),
        logging_max_bytes=_int(logging_cfg.get("max_bytes"), 10 * 1024 * 1024),
        audit_path=str(backend.get("audit_path") or "logs/audit.jsonl"),
        tables_page_size=_int(backend.get("tables_page_size"), 50),
        display_max_rows=_int(backend.get("display_max_rows"), 100),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()
