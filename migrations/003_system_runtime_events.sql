-- System database (data_agent_system): runtime SSE event history.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS runtime_events (
  event_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  request_id VARCHAR(255) NOT NULL,
  owner_user_id VARCHAR(255) NOT NULL,
  event_json LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_runtime_events_request (owner_user_id, request_id, event_id)
);