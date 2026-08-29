-- runtime.sqlite：会话、任务、HITL。LangGraph Checkpoint 在 checkpoint.sqlite，不要写进本库。

CREATE TABLE IF NOT EXISTS thread (
  thread_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
  task_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('query', 'write', 'followup_filter', 'followup_requery', 'clarify')),
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (thread_id) REFERENCES thread(thread_id)
);

CREATE TABLE IF NOT EXISTS hitl_interrupt (
  interrupt_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  task_id TEXT,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
  expires_at TEXT NOT NULL,
  resolved_at TEXT,
  resolver_user_id TEXT,
  FOREIGN KEY (thread_id) REFERENCES thread(thread_id)
);

CREATE INDEX IF NOT EXISTS idx_task_thread ON task(thread_id);
CREATE INDEX IF NOT EXISTS idx_hitl_thread ON hitl_interrupt(thread_id, status);
