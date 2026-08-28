-- results.sqlite：查询结果元数据。Parquet 文件在 data/results/，原始行不进本库。

CREATE TABLE IF NOT EXISTS query_result (
  result_id TEXT PRIMARY KEY,
  thread_id TEXT,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('WRITING', 'READY', 'EXPIRED', 'DELETED')),
  parquet_path TEXT,
  row_count INTEGER,
  columns_json TEXT NOT NULL DEFAULT '[]',
  parent_result_id TEXT,
  time_range_json TEXT,
  permission_version INTEGER NOT NULL,
  catalog_version INTEGER NOT NULL,
  schema_version INTEGER NOT NULL,
  data_as_of TEXT,
  metric_versions_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_result_user_status ON query_result(user_id, status);
CREATE INDEX IF NOT EXISTS idx_result_parent ON query_result(parent_result_id);
