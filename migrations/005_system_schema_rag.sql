-- System database (data_agent_system): Schema RAG authority, lexical
-- documents and active Milvus manifest. The catalog_snapshots row references
-- the business database (data_agent_ecommerce) by name only; it never opens
-- a cross-database connection.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS catalog_snapshots (
  source_id VARCHAR(128) NOT NULL,
  catalog_version VARCHAR(64) NOT NULL,
  database_name VARCHAR(128) NOT NULL,
  snapshot_json LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (source_id, catalog_version)
);

CREATE TABLE IF NOT EXISTS catalog_object_metadata (
  object_id VARCHAR(128) PRIMARY KEY,
  description TEXT NOT NULL,
  FOREIGN KEY (object_id) REFERENCES catalog_objects(object_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_field_metadata (
  field_id VARCHAR(160) PRIMARY KEY,
  ordinal_position INT NOT NULL,
  column_type VARCHAR(255) NOT NULL,
  description TEXT NOT NULL,
  FOREIGN KEY (field_id) REFERENCES catalog_fields(field_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_metric_sources (
  metric_id VARCHAR(128) NOT NULL,
  source_id VARCHAR(128) NOT NULL,
  PRIMARY KEY (metric_id, source_id),
  FOREIGN KEY (metric_id) REFERENCES metric_definitions(metric_id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES catalog_sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_relation_sources (
  relation_id VARCHAR(128) NOT NULL,
  source_id VARCHAR(128) NOT NULL,
  origin VARCHAR(16) NOT NULL,
  PRIMARY KEY (relation_id, source_id),
  FOREIGN KEY (relation_id) REFERENCES table_relations(relation_id) ON DELETE CASCADE,
  FOREIGN KEY (source_id) REFERENCES catalog_sources(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_search_documents (
  document_id VARCHAR(160) PRIMARY KEY,
  layer VARCHAR(32) NOT NULL,
  document_kind VARCHAR(32) NOT NULL,
  target_id VARCHAR(160) NOT NULL,
  source_id VARCHAR(128) NOT NULL,
  object_id VARCHAR(128) NOT NULL DEFAULT '',
  catalog_version VARCHAR(64) NOT NULL,
  content TEXT NOT NULL,
  document_length INT NOT NULL,
  INDEX idx_catalog_docs_scope (source_id, catalog_version, layer, document_kind),
  INDEX idx_catalog_docs_target (target_id, catalog_version)
);

CREATE TABLE IF NOT EXISTS catalog_search_terms (
  document_id VARCHAR(160) NOT NULL,
  term VARCHAR(128) NOT NULL,
  term_frequency INT NOT NULL,
  PRIMARY KEY (document_id, term),
  INDEX idx_catalog_terms_term (term),
  FOREIGN KEY (document_id) REFERENCES catalog_search_documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_index_manifests (
  manifest_id VARCHAR(96) PRIMARY KEY,
  catalog_version VARCHAR(64) NOT NULL,
  index_version VARCHAR(64) NOT NULL,
  embedding_provider VARCHAR(128) NOT NULL,
  embedding_model VARCHAR(255) NOT NULL,
  embedding_dimension INT NOT NULL,
  collections_json JSON NOT NULL,
  document_counts_json JSON NOT NULL,
  status VARCHAR(16) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  activated_at DATETIME(6) NULL,
  INDEX idx_catalog_manifest_active (status, catalog_version, activated_at),
  UNIQUE KEY uq_catalog_manifest_version (catalog_version, index_version)
);