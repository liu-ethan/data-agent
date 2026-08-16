# Migrations

Apply the numbered migrations in order after `scripts/mysql_schema.sql`, using
the migration account. `005_schema_rag.sql` adds collected snapshots, BM25
documents/terms, physical relation ownership and the active Milvus manifest;
`006_checkpoint_history.sql` adds the immutable per-super-step recovery chain;
`007_password_auth.sql` adds the password verifier used by account login.

The runtime never uses the migration account for analytical reads: Query
execution uses `agent_reader`, while catalog collection, checkpoints, artifacts,
results and authorization are control-plane records. After applying migration
006 and curating metrics/aliases, run:

```bash
python3.12 scripts/index_catalog.py --collect-only
python3.12 scripts/index_catalog.py --index-only
```

The combined `python3.12 scripts/index_catalog.py` command performs both steps.
It never creates or seeds business tables.
