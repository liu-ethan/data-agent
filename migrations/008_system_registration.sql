-- System database (data_agent_system): invite codes (CLI-issued) and
-- LLM-generated thread titles. Idempotent so the 001 -> 008 sequence can be
-- re-applied safely.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS invite_codes (
  code VARCHAR(64) PRIMARY KEY,
  role_name VARCHAR(32) NOT NULL,
  max_uses INT NOT NULL DEFAULT 1,
  used_count INT NOT NULL DEFAULT 0,
  policy_version VARCHAR(128) NOT NULL,
  created_by VARCHAR(255) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK (role_name IN ('USER', 'ADMIN')),
  CHECK (used_count <= max_uses),
  KEY idx_invite_codes_role (role_name, active)
);

CREATE TABLE IF NOT EXISTS thread_titles (
  thread_id VARCHAR(255) PRIMARY KEY,
  title VARCHAR(64) NOT NULL,
  generated_at DATETIME(6) NOT NULL,
  KEY idx_thread_titles_generated (generated_at)
);