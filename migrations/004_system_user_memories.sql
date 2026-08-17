-- System database (data_agent_system): confirmed, versioned long-term
-- preferences. Shared schema and metric facts do not belong here; they
-- remain in the catalog tables.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS user_memories (
  memory_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(255) NOT NULL,
  memory_key VARCHAR(64) NOT NULL,
  value_json LONGTEXT NOT NULL,
  source VARCHAR(32) NOT NULL,
  version INT NOT NULL,
  confirmed_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_user_memory_key (user_id, memory_key),
  KEY idx_user_memories_user (user_id)
);