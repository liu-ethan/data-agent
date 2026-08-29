-- name: insert_write_receipt
INSERT INTO da_write_receipt
  (operation_id, request_hash, operation_type, status, payload_json)
VALUES (:operation_id, :request_hash, :operation_type, :status, :payload_json)

-- name: insert_write_audit
INSERT INTO da_write_audit
  (audit_id, operation_id, actor_user_id, operation_type,
   target_table, target_pk, before_json, after_json)
VALUES (:audit_id, :operation_id, :actor_user_id, :operation_type,
        :target_table, :target_pk, :before_json, :after_json)

-- name: update_write_receipt_committed
UPDATE da_write_receipt
SET status = :status, affected_rows = :affected_rows, audit_id = :audit_id
WHERE operation_id = :operation_id

-- name: select_write_receipt
SELECT operation_id, request_hash, operation_type, status,
       affected_rows, audit_id FROM da_write_receipt
WHERE operation_id = :operation_id

-- name: lock_target_rows
SELECT id, row_version, status, inventory_qty FROM `{table}` WHERE id IN :ids FOR UPDATE

-- name: precheck_target_rows
SELECT id, row_version, status, inventory_qty FROM `{table}` WHERE id IN :ids

-- name: search_sku
SELECT id, sku_name FROM dim_sku WHERE sku_name LIKE :q LIMIT {limit}
