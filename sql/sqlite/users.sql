-- users.sqlite：本地登录用户与权限版本。不要放 Schema / 向量 / 结果。

CREATE TABLE IF NOT EXISTS app_user (
  user_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('analyst', 'operator')),
  tenant_id TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_permission (
  user_id TEXT NOT NULL,
  permission_version INTEGER NOT NULL,
  allowed_tables_json TEXT NOT NULL,
  allowed_columns_json TEXT NOT NULL,
  allowed_metrics_json TEXT NOT NULL,
  allowed_write_ops_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, permission_version),
  FOREIGN KEY (user_id) REFERENCES app_user(user_id)
);
