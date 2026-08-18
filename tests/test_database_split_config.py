"""MVP acceptance tests for the spec 01 database split.

These tests do not require a live MySQL: they statically verify that the
business schema and system migrations correctly describe two distinct
databases, that the cross-database FK has been dropped, and that runtime
classes point at the right database based on config.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.app.config import load_settings
from backend.app.repositories.data import MySQLDataRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUSINESS_SCHEMA = PROJECT_ROOT / "scripts" / "business_schema.sql"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
MYSQL_ENV_SH = PROJECT_ROOT / "scripts" / "mysql_env.sh"
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup_databases.sh"
MOCK_DATA_SH = PROJECT_ROOT / "scripts" / "mock_mysql_data.sh"
CATALOG_SEED = PROJECT_ROOT / "scripts" / "catalog_seed.sql"
RUNTIME_SEED = PROJECT_ROOT / "scripts" / "runtime_seed.sql"
CONFIG_YAML = PROJECT_ROOT / "config.yaml"

SPEC_01_BUSINESS_TABLES = {
    "shops", "users", "categories", "products",
    "orders", "order_items", "refunds", "refund_items",
}

SYSTEM_TABLES = {
    "catalog_sources", "catalog_objects", "catalog_fields", "metric_definitions",
    "business_presets", "table_relations", "entity_aliases", "permission_policies",
    "catalog_snapshots", "catalog_object_metadata", "catalog_field_metadata",
    "catalog_metric_sources", "catalog_relation_sources",
    "catalog_search_documents", "catalog_search_terms", "catalog_index_manifests",
    "app_users", "app_user_shop_scopes", "invite_codes",
    "runtime_checkpoints", "runtime_idempotency", "runtime_results",
    "runtime_events", "runtime_checkpoint_history",
    "conversation_messages", "conversation_artifacts",
    "thread_titles", "user_memories", "mutation_audit",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _create_table_statements(text: str) -> list[str]:
    """Yield the name and body of every CREATE TABLE statement."""
    pattern = re.compile(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+`?(\w+)`?\s*\(",
        flags=re.IGNORECASE,
    )
    results: list[str] = []
    for match in pattern.finditer(text):
        name = match.group(1)
        start = match.end()
        depth = 1
        index = start
        while depth > 0 and index < len(text):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        body = text[start:index - 1]
        results.append((name, body))
    return results


def _strip_sql_comments(text: str) -> str:
    """Remove SQL ``-- line`` comments so we can inspect statements."""
    return re.sub(r"--[^\n]*", "", text)


def _first_use_database(text: str) -> str | None:
    cleaned = _strip_sql_comments(text)
    match = re.search(r"USE\s+`?(\w+)`?\s*;", cleaned, flags=re.IGNORECASE)
    return match.group(1) if match else None


def test_business_schema_creates_only_eight_business_tables():
    created = {name.lower() for name, _ in _create_table_statements(_read(BUSINESS_SCHEMA))}
    assert created == SPEC_01_BUSINESS_TABLES
    unwanted = created & SYSTEM_TABLES
    assert not unwanted, f"business schema leaked system tables: {unwanted}"


def test_business_schema_targets_business_database():
    assert _first_use_database(_read(BUSINESS_SCHEMA)) == "data_agent_ecommerce"


@pytest.mark.parametrize("migration", [
    "001_system_identity_and_runtime.sql",
    "002_system_catalog.sql",
    "003_system_runtime_events.sql",
    "004_system_user_memories.sql",
    "005_system_schema_rag.sql",
    "006_system_checkpoint_history.sql",
    "007_system_password_auth.sql",
    "008_system_registration.sql",
    "009_system_user_memory_history.sql",
    "010_system_mutation_audit.sql",
])
def test_system_migrations_target_system_database(migration):
    path = MIGRATIONS_DIR / migration
    assert path.exists(), f"missing migration: {migration}"
    assert _first_use_database(_read(path)) == "data_agent_system", \
        f"{migration} must USE data_agent_system"


@pytest.mark.parametrize("migration", [
    "001_system_identity_and_runtime.sql",
    "002_system_catalog.sql",
    "003_system_runtime_events.sql",
    "004_system_user_memories.sql",
    "005_system_schema_rag.sql",
    "006_system_checkpoint_history.sql",
    "007_system_password_auth.sql",
    "008_system_registration.sql",
    "009_system_user_memory_history.sql",
    "010_system_mutation_audit.sql",
])
def test_system_migrations_never_create_business_tables(migration):
    path = MIGRATIONS_DIR / migration
    created = {name.lower() for name, _ in _create_table_statements(_read(path))}
    leaked = created & SPEC_01_BUSINESS_TABLES
    assert not leaked, f"{migration} must not create business tables: {leaked}"


def test_cross_database_fk_on_app_user_shop_scopes_is_dropped():
    migration = _read(MIGRATIONS_DIR / "001_system_identity_and_runtime.sql")
    rows = [body for name, body in _create_table_statements(migration)
            if name.lower() == "app_user_shop_scopes"]
    assert rows, "app_user_shop_scopes table definition must exist"
    body = rows[0].lower()
    assert "references shops" not in body, "cross-database FK to shops must be dropped"
    assert "shop_id varchar(64)" in body, "shop_id column must remain a denormalized string"


def test_seeds_target_their_own_database():
    assert _first_use_database(_read(CATALOG_SEED)) == "data_agent_system"
    assert _first_use_database(_read(RUNTIME_SEED)) == "data_agent_system"


def test_mock_mysql_data_default_targets_business_database():
    text = _read(MOCK_DATA_SH)
    assert "data_agent_ecommerce" in text
    assert 'MYSQL_DATABASE:-data_agent"' not in text or "data_agent_ecommerce" in text


def test_config_yaml_is_the_single_source_of_truth():
    text = _read(CONFIG_YAML)
    assert "business_database: data_agent_ecommerce" in text, "config.yaml missing business_database"
    assert "system_database: data_agent_system" in text, "config.yaml missing system_database"
    assert "\n  database: data_agent\n" not in text, "config.yaml still references the legacy single database field"
    # Stale config files that have been consolidated into config.yaml must
    # not return. Re-introducing any of them re-opens the spec-01 §11 split.
    for stale in (PROJECT_ROOT / "config template.yaml",
                  PROJECT_ROOT / ".secrets.yaml",
                  PROJECT_ROOT / ".env.example"):
        assert not stale.exists(), f"stale config file present: {stale}"


def test_config_exposes_business_and_system_databases():
    settings = load_settings(CONFIG_YAML)
    assert settings.mysql.get("business_database") == "data_agent_ecommerce"
    assert settings.mysql.get("system_database") == "data_agent_system"
    assert settings.mysql.get("business_database") != settings.mysql.get("system_database")


def test_mysql_data_repository_engine_targets_business_database(monkeypatch):
    """MySQLDataRepository's SQLAlchemy engine must bind to business_database."""

    captured: dict[str, object] = {}

    class _StubEngine:
        def __init__(self, url, **kwargs):
            self.url = url
            captured["url"] = url
            captured["kwargs"] = kwargs

    import backend.app.repositories.data as data_module
    monkeypatch.setattr(data_module, "create_engine", _StubEngine)

    config = {
        "host": "localhost",
        "port": 3306,
        "business_database": "data_agent_ecommerce",
        "charset": "utf8mb4",
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle_seconds": 1800,
        "accounts": {"reader": {"username": "agent_reader", "password": "secret"}},
    }
    MySQLDataRepository(config)
    url = captured["url"]
    assert str(url).startswith("mysql+pymysql://"), url
    assert url.database == "data_agent_ecommerce"


def test_mysql_url_defaults_to_system_database_for_runtime_persistence():
    from backend.app.repositories.runtime import mysql_url

    config = {
        "host": "localhost", "port": 3306,
        "business_database": "data_agent_ecommerce",
        "system_database": "data_agent_system",
        "charset": "utf8mb4",
        "accounts": {"control": {"username": "agent_control", "password": "x"}},
    }
    url = mysql_url(config)
    assert url.database == "data_agent_system"

    business = mysql_url(config, database=config["business_database"])
    assert business.database == "data_agent_ecommerce"


def test_mysql_env_sh_administers_two_databases():
    text = _read(MYSQL_ENV_SH)
    assert "MYSQL_BUSINESS_DATABASE" in text
    assert "MYSQL_SYSTEM_DATABASE" in text
    assert "MYSQL_BUSINESS_DATABASE:-data_agent_ecommerce" in text
    assert "MYSQL_SYSTEM_DATABASE:-data_agent_system" in text
    assert r"GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.shops" in text
    assert r"GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.thread_titles" in text
    assert r"GRANT SELECT, INSERT ON \`$MYSQL_SYSTEM_DATABASE\`.user_memory_history" in text
    # The reader account must never receive a top-level grant against the system database.
    assert not re.search(r"GRANT[^;]*MYSQL_SYSTEM_DATABASE[^;]*TO\s+'\$MYSQL_READER_USER'",
                         text), "reader must not be granted anything against the system database"


def test_setup_databases_script_targets_two_databases():
    text = _read(SETUP_SCRIPT)
    assert "data_agent_ecommerce" in text
    assert "data_agent_system" in text
    assert "scripts/business_schema.sql" in text
    assert "migrations/00?_system_*.sql" in text
    assert "scripts/mysql_env.sh harden" in text
    assert "scripts/mock_mysql_data.sh seed" in text
