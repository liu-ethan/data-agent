-- name: select_thread_owner
SELECT user_id FROM thread WHERE thread_id = ?

-- name: select_thread_title
SELECT title FROM thread WHERE thread_id = ? AND user_id = ?

-- name: update_thread_title
UPDATE thread SET title = ? WHERE thread_id = ? AND user_id = ?

-- name: list_threads
SELECT thread_id, user_id, title, created_at, updated_at
FROM thread WHERE user_id = ? ORDER BY updated_at DESC

-- name: delete_thread
DELETE FROM thread WHERE thread_id = ? AND user_id = ?

-- name: upsert_thread
INSERT INTO thread (thread_id, user_id, title, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(thread_id) DO UPDATE SET
  user_id = excluded.user_id,
  updated_at = excluded.updated_at
