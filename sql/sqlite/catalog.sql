-- catalog.sqlite：审核过的表/字段/关系/指标/写入操作。不要放向量或用户密码。

CREATE TABLE IF NOT EXISTS catalog_meta (
  catalog_version INTEGER PRIMARY KEY,
  mysql_database TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS schema_table (
  table_name TEXT PRIMARY KEY,
  business_name TEXT NOT NULL,
  domain TEXT NOT NULL,
  grain_description TEXT NOT NULL,
  comment TEXT,
  aliases_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS schema_column (
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  data_type TEXT NOT NULL,
  comment TEXT,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  is_sensitive INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (table_name, column_name),
  FOREIGN KEY (table_name) REFERENCES schema_table(table_name)
);

CREATE TABLE IF NOT EXISTS schema_relation (
  relation_id INTEGER PRIMARY KEY,
  left_table TEXT NOT NULL,
  left_col TEXT NOT NULL,
  right_table TEXT NOT NULL,
  right_col TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('one_to_one', 'one_to_many', 'many_to_one')),
  source TEXT NOT NULL CHECK (source IN ('fk', 'human')),
  version INTEGER NOT NULL DEFAULT 1,
  reviewed INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS metric_spec (
  metric_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  grain_table TEXT NOT NULL,
  formula TEXT NOT NULL,
  time_field TEXT NOT NULL,
  unit TEXT NOT NULL,
  filters_json TEXT NOT NULL,
  deps_json TEXT NOT NULL,
  needs_tables_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS write_op (
  operation_type TEXT PRIMARY KEY,
  target_table TEXT NOT NULL,
  allowed_columns_json TEXT NOT NULL,
  sql_template TEXT NOT NULL,
  max_affected_rows INTEGER NOT NULL,
  requires_hitl INTEGER NOT NULL DEFAULT 1,
  version_predicate TEXT NOT NULL
);
