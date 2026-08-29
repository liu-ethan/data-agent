-- name: insert_query_result
INSERT INTO query_result (
    result_id, thread_id, user_id, status, parquet_path,
    row_count, columns_json, parent_result_id, time_range_json,
    permission_version, catalog_version, schema_version,
    data_as_of, metric_versions_json, created_at, expires_at
) VALUES (?, ?, ?, 'WRITING', ?, NULL, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)

-- name: update_query_result_ready
UPDATE query_result
SET status = 'READY', row_count = ?, columns_json = ?, data_as_of = ?
WHERE result_id = ?

-- name: select_query_result
SELECT * FROM query_result WHERE result_id = ?

-- name: update_query_result_deleted
UPDATE query_result SET status = 'DELETED' WHERE result_id = ?

-- name: select_ready_expired
SELECT result_id FROM query_result WHERE status = 'READY' AND expires_at <= ?

-- name: select_expired_ids
SELECT result_id FROM query_result WHERE status = 'EXPIRED'

-- name: select_writing_ids
SELECT result_id FROM query_result WHERE status = 'WRITING'

-- name: select_status_expires
SELECT status, expires_at FROM query_result WHERE result_id = ?

-- name: update_query_result_expired
UPDATE query_result SET status = 'EXPIRED' WHERE result_id = ?

-- name: select_status
SELECT status FROM query_result WHERE result_id = ?
