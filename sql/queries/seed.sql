-- name: seed_delete_user_permission
DELETE FROM user_permission

-- name: seed_delete_app_user
DELETE FROM app_user

-- name: seed_insert_app_user
INSERT INTO app_user VALUES (?, ?, ?, ?, ?, ?, 1, ?)

-- name: seed_insert_user_permission
INSERT INTO user_permission VALUES (?, 1, ?, ?, ?, ?, ?)

-- name: seed_delete_write_op
DELETE FROM write_op

-- name: seed_delete_metric_spec
DELETE FROM metric_spec

-- name: seed_delete_schema_relation
DELETE FROM schema_relation

-- name: seed_delete_schema_column
DELETE FROM schema_column

-- name: seed_delete_schema_table
DELETE FROM schema_table

-- name: seed_delete_catalog_meta
DELETE FROM catalog_meta

-- name: seed_insert_catalog_meta
INSERT INTO catalog_meta VALUES (1, ?, ?, ?)

-- name: seed_insert_schema_table
INSERT INTO schema_table VALUES (?, ?, ?, ?, ?, '[]')

-- name: seed_insert_schema_relation
INSERT INTO schema_relation VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)

-- name: seed_insert_metric_spec
INSERT INTO metric_spec
  (metric_id, name, version, grain_table, formula, time_field, unit,
   filters_json, deps_json, needs_tables_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

-- name: seed_insert_write_op
INSERT INTO write_op VALUES (?, ?, ?, ?, ?, ?, ?)
