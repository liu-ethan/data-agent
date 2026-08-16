-- Registration flow: invite codes (CLI-issued) and LLM-generated thread titles.
-- Idempotent so the 002 -> 008 migration sequence can be re-run safely.

USE data_agent;

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

-- Grant runtime and CLI accounts the right to read/write the new control-plane tables.
-- Without these, the runtime registration endpoint and the invite-code CLI receive
-- 1142 "SELECT command denied" errors even though the tables exist.
GRANT SELECT, INSERT, UPDATE ON data_agent.invite_codes TO 'agent_control'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.invite_codes TO 'agent_migration'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.invite_codes TO 'agent_reader'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.invite_codes TO 'agent_writer'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.thread_titles TO 'agent_control'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.thread_titles TO 'agent_migration'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.thread_titles TO 'agent_reader'@'localhost';
GRANT SELECT, INSERT, UPDATE ON data_agent.thread_titles TO 'agent_writer'@'localhost';
-- Registration writes a fresh app_users row; the runtime control account needs
-- INSERT/UPDATE on identity tables in addition to the SELECT it already had.
GRANT INSERT, UPDATE ON data_agent.app_users TO 'agent_control'@'localhost';
GRANT INSERT, UPDATE ON data_agent.app_user_shop_scopes TO 'agent_control'@'localhost';