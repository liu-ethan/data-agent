-- name: select_user_by_username
SELECT user_id, username, password_hash, display_name, role
FROM app_user WHERE username = ? AND is_active = 1

-- name: select_user_by_id
SELECT user_id, username, password_hash, display_name, role
FROM app_user WHERE user_id = ? AND is_active = 1

-- name: insert_app_user
INSERT INTO app_user
  (user_id, username, password_hash, display_name, role, tenant_id, is_active, created_at)
VALUES (?, ?, ?, ?, ?, ?, 1, ?)

-- name: insert_user_permission
INSERT INTO user_permission
  (user_id, permission_version, allowed_tables_json, allowed_columns_json,
   allowed_metrics_json, allowed_write_ops_json, updated_at)
VALUES (?, 1, ?, ?, ?, ?, ?)

-- name: select_permissions
SELECT u.user_id, u.role, u.tenant_id, p.permission_version,
       p.allowed_tables_json, p.allowed_columns_json,
       p.allowed_metrics_json, p.allowed_write_ops_json
FROM app_user u
JOIN user_permission p ON p.user_id = u.user_id
WHERE u.user_id = ? AND u.is_active = 1
ORDER BY p.permission_version DESC
LIMIT 1
