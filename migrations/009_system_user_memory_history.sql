-- System database (data_agent_system): audit history for confirmed
-- long-term preference overwrites. The current value remains in
-- user_memories; this table is append-only.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS user_memory_history (
  history_id VARCHAR(64) PRIMARY KEY,
  memory_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(255) NOT NULL,
  memory_key VARCHAR(64) NOT NULL,
  old_value_json LONGTEXT NULL,
  new_value_json LONGTEXT NOT NULL,
  source VARCHAR(32) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_user_memory_history_memory (memory_id, created_at),
  KEY idx_user_memory_history_user_key (user_id, memory_key, created_at)
);
