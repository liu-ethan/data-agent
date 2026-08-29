-- name: select_manifest_version
SELECT catalog_version FROM embedding_manifest ORDER BY id DESC LIMIT 1

-- name: delete_table_embedding
DELETE FROM table_embedding

-- name: delete_column_embedding
DELETE FROM column_embedding

-- name: delete_embedding_manifest
DELETE FROM embedding_manifest

-- name: insert_table_embedding
INSERT INTO table_embedding
  (table_name, catalog_version, text, vector)
VALUES (?, ?, ?, ?)

-- name: insert_column_embedding
INSERT INTO column_embedding
  (table_name, column_name, catalog_version, text, vector)
VALUES (?, ?, ?, ?, ?)

-- name: insert_embedding_manifest
INSERT INTO embedding_manifest (model, dim, catalog_version, built_at)
VALUES (?, ?, ?, datetime('now'))

-- name: select_table_embeddings
SELECT table_name, vector FROM table_embedding
WHERE catalog_version = ?

-- name: select_column_embeddings
SELECT table_name, column_name, vector FROM column_embedding
WHERE catalog_version = ?
