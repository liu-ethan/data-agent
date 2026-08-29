from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MysqlAccount(BaseModel):
    user: str
    password: str = ""


class MysqlSettings(BaseModel):
    host: str
    port: int
    database: str
    charset: str = "utf8mb4"
    admin: MysqlAccount
    reader: MysqlAccount
    writer: MysqlAccount


class LlmModels(BaseModel):
    coordinator: str = ""
    query_skeleton: str = ""
    write_plan: str = ""
    retrieval: str = ""


class LlmSettings(BaseModel):
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60
    models: LlmModels = Field(default_factory=LlmModels)


class EmbeddingSettings(BaseModel):
    available: bool = True
    base_url: str
    api_key: str
    model: str
    dim: int


class AppSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    timezone: str = "Asia/Shanghai"


class SqliteSettings(BaseModel):
    dir: str
    users: str
    catalog: str
    embeddings: str
    checkpoint: str
    runtime: str
    results: str


class ResultsSettings(BaseModel):
    dir: str
    ttl_hours: int = 1
    max_rows: int = 100000
    max_bytes: str = "256MB"


class QuerySettings(BaseModel):
    timeout_seconds: int = 30
    max_explain_rows: int = 5000000


class WriteSettings(BaseModel):
    max_affected_rows: int = 100
    approval_ttl_minutes: int = 15


class SchemaRagSettings(BaseModel):
    table_top_k: int = 5
    column_top_k: int = 10
    max_gap_rounds: int = 2


class AuthUser(BaseModel):
    username: str
    password: str
    display_name: str
    role: Literal["analyst", "operator"]


class AuthSettings(BaseModel):
    mode: Literal["local_password", "off"] = "local_password"
    users: list[AuthUser] = Field(default_factory=list)
    jwt_secret: str = "change-me-change-me-change-me-32b"
    jwt_ttl_hours: int = 24


class WriteOp(BaseModel):
    type: str


class DatasetSettings(BaseModel):
    mode: str = "local"
    metrics: str = "approved"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid")

    mysql: MysqlSettings
    llm: LlmSettings
    embedding: EmbeddingSettings
    app: AppSettings
    sqlite: SqliteSettings
    results: ResultsSettings
    query: QuerySettings
    write: WriteSettings
    schema_rag: SchemaRagSettings
    auth: AuthSettings
    write_ops: list[WriteOp]
    dataset: DatasetSettings


def _resolve_config_path(path: str | None = None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("DATA_AGENT_CONFIG")
    if env:
        return Path(env)
    return Path("config.yaml")


def load_settings(path: str | None = None) -> Settings:
    resolved = _resolve_config_path(path)
    text = resolved.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"config is not a mapping: {resolved}")
    return Settings.model_validate(data)
