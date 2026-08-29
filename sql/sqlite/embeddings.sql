-- embeddings.sqlite：Schema RAG 向量。不要放用户或指标公式。

CREATE TABLE IF NOT EXISTS embedding_manifest (
  id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  catalog_version INTEGER NOT NULL,
  built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS table_embedding (
  table_name TEXT NOT NULL,
  catalog_version INTEGER NOT NULL,
  text TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (table_name, catalog_version)
);

CREATE TABLE IF NOT EXISTS column_embedding (
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  catalog_version INTEGER NOT NULL,
  text TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (table_name, column_name, catalog_version)
);
