-- Immutable per-super-step checkpoint history. runtime_checkpoints remains the
-- optimistic-locking current pointer used on the request hot path.

CREATE TABLE IF NOT EXISTS runtime_checkpoint_history (
  checkpoint_id VARCHAR(64) PRIMARY KEY,
  thread_id VARCHAR(255) NOT NULL,
  state_version INT NOT NULL,
  parent_checkpoint_id VARCHAR(64) NULL,
  status VARCHAR(32) NOT NULL,
  state_json LONGTEXT NOT NULL,
  checkpoint_json LONGTEXT NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL UNIQUE,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_checkpoint_history_thread_version (thread_id, state_version),
  KEY idx_checkpoint_history_thread (thread_id, state_version)
);
