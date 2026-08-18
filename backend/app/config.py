"""Configuration with explicit env > local secret file > YAML precedence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .errors import RuntimeAgentError


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = "data-runtime-agent"
    environment: str = "local"
    timezone: str = "Asia/Shanghai"
    cors_origins: list[str] = Field(default_factory=list)
    api_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")
    app: AppSettings = Field(default_factory=AppSettings)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def server(self) -> dict[str, Any]:
        return self.raw.get("server", {})

    @property
    def mysql(self) -> dict[str, Any]:
        return self.raw.get("mysql", {})

    @property
    def read_query(self) -> dict[str, Any]:
        return self.raw.get("read_query", {})

    @property
    def retrieval_budget(self) -> dict[str, Any]:
        return self.raw.get("retrieval_budget", {})

    @property
    def runtime_agent(self) -> dict[str, Any]:
        return self.raw.get("runtime_agent", {})

    @property
    def permissions(self) -> dict[str, Any]:
        return self.raw.get("permissions", {})

    @property
    def write_query(self) -> dict[str, Any]:
        return self.raw.get("write_query", {})


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_overrides() -> dict[str, Any]:
    # Only known non-secret settings are mapped here. Secret values are read by
    # clients from their own config object and are never logged or serialized.
    result: dict[str, Any] = {}
    mappings = {
        "DRA_ENVIRONMENT": ("app", "environment"),
        "DRA_CORS_ORIGINS": ("app", "cors_origins"),
        "DRA_MYSQL_HOST": ("mysql", "host"),
        "DRA_MYSQL_PORT": ("mysql", "port"),
        "DRA_MYSQL_BUSINESS_DATABASE": ("mysql", "business_database"),
        "DRA_MYSQL_SYSTEM_DATABASE": ("mysql", "system_database"),
        "DRA_MYSQL_MIGRATION_USERNAME": ("mysql", "accounts", "migration", "username"),
        "DRA_MYSQL_CONTROL_USERNAME": ("mysql", "accounts", "control", "username"),
        "DRA_MYSQL_READER_USERNAME": ("mysql", "accounts", "reader", "username"),
        "DRA_MYSQL_WRITER_USERNAME": ("mysql", "accounts", "writer", "username"),
        "DRA_LLM_BASE_URL": ("llm", "base_url"),
        "DRA_LLM_API_KEY": ("llm", "api_key"),
        "DRA_LLM_MODEL": ("llm", "model"),
        "DRA_LLM_PROVIDER": ("llm", "provider"),
        "DRA_LLM_PROTOCOL": ("llm", "protocol"),
        "DRA_JWT_SECRET": ("auth", "jwt", "secret"),
        "DRA_MYSQL_MIGRATION_PASSWORD": ("mysql", "accounts", "migration", "password"),
        "DRA_MYSQL_CONTROL_PASSWORD": ("mysql", "accounts", "control", "password"),
        "DRA_MYSQL_READER_PASSWORD": ("mysql", "accounts", "reader", "password"),
        "DRA_MYSQL_WRITER_PASSWORD": ("mysql", "accounts", "writer", "password"),
        "DRA_MILVUS_URI": ("milvus", "uri"),
        "DRA_MILVUS_TOKEN": ("milvus", "token"),
        "DRA_MILVUS_ENABLED": ("milvus", "enabled"),
    }
    for env_name, path in mappings.items():
        if env_name not in os.environ:
            continue
        cursor = result
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        value: Any = os.environ[env_name]
        if path[-1] in {"port"}:
            value = int(value)
        if path[-1] == "enabled":
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        if path[-1] == "cors_origins":
            value = [item.strip() for item in value.split(",") if item.strip()]
        cursor[path[-1]] = value
    return result


def load_settings(path: str | Path = "config.yaml", secret_path: str | Path | None = None) -> Settings:
    config_path = Path(path)
    values: dict[str, Any] = {}
    if config_path.exists():
        values = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    local_secret_path = Path(secret_path) if secret_path else Path(".secrets.yaml")
    if local_secret_path.exists():
        secret_values = yaml.safe_load(local_secret_path.read_text(encoding="utf-8")) or {}
        values = _deep_merge(values, secret_values)
    values = _deep_merge(values, _env_overrides())
    app = values.get("app", {})
    if not app.get("cors_origins") or "*" in app.get("cors_origins", []):
        raise RuntimeAgentError("CONFIG_MISSING", "cors_origins must contain explicit origins")
    return Settings(app=app, raw=values)


def redact_mapping(value: Any, redacted_keys: tuple[str, ...] =
                   ("password", "api_key", "token", "secret", "jwt", "phone", "id_card")) -> Any:
    """Return a safe copy suitable for logs; never mutate loaded config."""
    if isinstance(value, dict):
        return {key: "<redacted>" if any(k in key.lower() for k in redacted_keys)
                else redact_mapping(item, redacted_keys) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_mapping(item, redacted_keys) for item in value]
    if isinstance(value, SecretStr):
        return "<redacted>"
    return value
