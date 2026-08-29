-- name: select_catalog_version
SELECT MAX(catalog_version) AS catalog_version FROM catalog_meta

-- name: select_schema_tables
SELECT * FROM schema_table ORDER BY table_name

-- name: select_schema_columns
SELECT * FROM schema_column ORDER BY table_name, column_name

-- name: select_reviewed_relations
SELECT left_table, right_table, left_col, right_col,
       cardinality, source, version
FROM schema_relation
WHERE reviewed = 1 AND source IN ('fk', 'human')
ORDER BY relation_id

-- name: select_metrics
SELECT * FROM metric_spec ORDER BY metric_id

-- name: select_write_ops
SELECT * FROM write_op ORDER BY operation_type

-- name: select_metric_by_id
SELECT * FROM metric_spec WHERE metric_id = ?

-- name: select_max_catalog_version
SELECT MAX(catalog_version) FROM catalog_meta

-- name: schema_table_exists
SELECT 1 FROM schema_table WHERE table_name = ?

-- name: update_schema_table_comment
UPDATE schema_table SET comment = ? WHERE table_name = ?

-- name: insert_schema_table
INSERT INTO schema_table VALUES (?, ?, ?, ?, ?, '[]')

-- name: delete_schema_columns
DELETE FROM schema_column

-- name: insert_schema_column
INSERT INTO schema_column VALUES (?, ?, ?, ?, '[]', 0)

-- name: delete_schema_relations
DELETE FROM schema_relation

-- name: insert_schema_relation
INSERT INTO schema_relation VALUES (?, ?, ?, ?, ?, 'many_to_one', 'fk', 1, 1)

-- name: insert_catalog_meta
INSERT INTO catalog_meta VALUES (?, ?, ?, ?)

-- name: mysql_information_schema_tables
SELECT TABLE_NAME AS table_name, TABLE_COMMENT AS comment
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'
  AND TABLE_NAME IN :tables

-- name: mysql_information_schema_columns
SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
       DATA_TYPE AS data_type, COLUMN_COMMENT AS comment
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = :db AND TABLE_NAME IN :tables
ORDER BY TABLE_NAME, ORDINAL_POSITION

-- name: mysql_information_schema_fks
SELECT TABLE_NAME AS left_table, COLUMN_NAME AS left_col,
       REFERENCED_TABLE_NAME AS right_table,
       REFERENCED_COLUMN_NAME AS right_col
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = :db
  AND REFERENCED_TABLE_NAME IS NOT NULL
  AND TABLE_NAME IN :tables
  AND REFERENCED_TABLE_NAME IN :tables

-- name: mysql_list_tables_in_schema
SELECT table_name FROM information_schema.tables
WHERE table_schema = DATABASE()
