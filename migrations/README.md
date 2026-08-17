# Migrations

Apply the numbered migrations in order **against the system database
(`data_agent_system`)**, using the migration account. The eight business
tables live in `scripts/business_schema.sql` and are applied to the business
database (`data_agent_ecommerce`).

The system migrations cover:

- `001_system_identity_and_runtime.sql` — `app_users`, `app_user_shop_scopes`
  (without the cross-database shop_id FK), `runtime_checkpoints`,
  `runtime_idempotency`, `runtime_results`, `conversation_messages`,
  `conversation_artifacts`.
- `002_system_catalog.sql` — `catalog_sources`, `catalog_objects`,
  `catalog_fields`, `metric_definitions`, `business_presets`,
  `table_relations`, `entity_aliases`, `permission_policies`.
- `003_system_runtime_events.sql` — `runtime_events`.
- `004_system_user_memories.sql` — `user_memories`.
- `005_system_schema_rag.sql` — `catalog_snapshots`, RAG lexical documents and
  active Milvus manifest.
- `006_system_checkpoint_history.sql` — immutable per-super-step recovery
  chain.
- `007_system_password_auth.sql` — password verifier used by account login.
- `008_system_registration.sql` — invite codes and LLM-generated thread titles.
- `009_system_user_memory_history.sql` — append-only audit of long-term preference overwrites.

The runtime never uses the migration account for analytical reads: Query
execution uses `agent_reader`, which only connects to the business database.
Identity, runtime state, sessions and memories are control-plane records owned
by `agent_control` against the system database. After applying migration 006
and curating metrics/aliases, run:

```bash
python3.12 scripts/index_catalog.py --collect-only
python3.12 scripts/index_catalog.py --index-only
```

The combined `python3.12 scripts/index_catalog.py` command performs both steps.
It never creates or seeds business tables.

## One-shot setup

For a fresh MySQL instance:

```bash
MYSQL_ROOT_PASSWORD='...' \
MYSQL_MIGRATION_PASSWORD='...' MYSQL_CONTROL_PASSWORD='...' \
MYSQL_READER_PASSWORD='...'   MYSQL_WRITER_PASSWORD='...' \
  bash scripts/setup_databases.sh
```

The script creates both databases, the four application accounts, applies the
business DDL and system migrations, applies least-privilege grants and writes
the deterministic seed. Set `SKIP_HARDEN=1` or `SKIP_SEED=1` to opt out of
individual steps.