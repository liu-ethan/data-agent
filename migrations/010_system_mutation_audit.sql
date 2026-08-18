-- System database: durable audit trail for Admin WriteGateway commits.
-- Apply with the agent_migration account. Runtime writes go through
-- agent_control; agent_writer never touches this table.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS mutation_audit (
  audit_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL,
  request_id VARCHAR(255) NOT NULL,
  preview_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL,
  operation VARCHAR(16) NOT NULL,
  table_name VARCHAR(64) NOT NULL,
  filters_json LONGTEXT NOT NULL,
  changes_json LONGTEXT NOT NULL,
  before_json LONGTEXT NOT NULL,
  after_json LONGTEXT NOT NULL,
  decision VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  affected_rows INT NOT NULL,
  data_version VARCHAR(128) NOT NULL,
  permission_policy_version VARCHAR(128) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_mutation_audit_idempotency (idempotency_key),
  KEY idx_mutation_audit_user_created (user_id, created_at)
);
