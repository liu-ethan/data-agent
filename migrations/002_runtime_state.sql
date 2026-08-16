-- Durable application identity, authorization and runtime state.
-- Apply with agent_migration; agent_reader only needs SELECT on catalog/business
-- data and must not have access to these application-control tables in a
-- production deployment (grant separation is enforced by deployment SQL).

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

CREATE TABLE IF NOT EXISTS app_user_shop_scopes (
  user_id VARCHAR(255) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  policy_version VARCHAR(128) NOT NULL,
  PRIMARY KEY (user_id, shop_id),
  CONSTRAINT fk_app_scope_user FOREIGN KEY (user_id) REFERENCES app_users(user_id),
  CONSTRAINT fk_app_scope_shop FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
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
