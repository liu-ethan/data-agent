-- System database (data_agent_system): durable application identity, runtime
-- state, results, artifacts and conversation history. Apply with the
-- agent_migration account. The agent_reader account has no access to this
-- database; the agent_control account owns CRUD on every control-plane table.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS app_users (
  user_id VARCHAR(255) PRIMARY KEY,
  password_hash VARCHAR(255) NULL,
  role_name VARCHAR(32) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  policy_version VARCHAR(128) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  CHECK (role_name IN ('USER', 'ADMIN'))
);

-- app_user_shop_scopes.shop_id is a denormalized string reference to the
-- business database. Cross-database FOREIGN KEY constraints are not used:
-- InnoDB does not reliably enforce them across databases and the runtime
-- validates membership at request time using catalog_objects, not the
-- shops row directly.

CREATE TABLE IF NOT EXISTS app_user_shop_scopes (
  user_id VARCHAR(255) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  policy_version VARCHAR(128) NOT NULL,
  PRIMARY KEY (user_id, shop_id),
  CONSTRAINT fk_app_scope_user FOREIGN KEY (user_id) REFERENCES app_users(user_id)
);

CREATE TABLE IF NOT EXISTS runtime_checkpoints (
  thread_id VARCHAR(255) PRIMARY KEY,
  state_version INT NOT NULL,
  state_json LONGTEXT NOT NULL,
  checkpoint_json LONGTEXT NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL UNIQUE,
  updated_at DATETIME(6) NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_idempotency (
  `key` VARCHAR(255) PRIMARY KEY,
  value_json LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_results (
  result_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  rows_json LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  KEY idx_runtime_results_owner_expiry (owner_user_id, expires_at)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
  message_id VARCHAR(64) PRIMARY KEY,
  thread_id VARCHAR(255) NOT NULL,
  user_id VARCHAR(255) NOT NULL,
  role VARCHAR(16) NOT NULL,
  content TEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_messages_thread_created (thread_id, created_at)
);

CREATE TABLE IF NOT EXISTS conversation_artifacts (
  artifact_id VARCHAR(64) PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  spec_json LONGTEXT NOT NULL,
  payload_json LONGTEXT NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  KEY idx_artifacts_owner_expiry (owner_user_id, expires_at)
);