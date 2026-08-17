-- System database (data_agent_system): versioned semantic catalog tables
-- for Spec 01. The eight business tables live in scripts/business_schema.sql;
-- this migration only owns catalog data.

USE data_agent_system;

CREATE TABLE IF NOT EXISTS catalog_sources (
  source_id VARCHAR(128) PRIMARY KEY, name VARCHAR(255) NOT NULL, domain VARCHAR(128) NOT NULL,
  catalog_version VARCHAR(64) NOT NULL, owner VARCHAR(128) NOT NULL, created_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_objects (
  object_id VARCHAR(128) PRIMARY KEY, source_id VARCHAR(128) NOT NULL, object_name VARCHAR(128) NOT NULL,
  grain VARCHAR(128) NOT NULL, object_type VARCHAR(32) NOT NULL, catalog_version VARCHAR(64) NOT NULL,
  FOREIGN KEY (source_id) REFERENCES catalog_sources(source_id)
);
CREATE TABLE IF NOT EXISTS catalog_fields (
  field_id VARCHAR(160) PRIMARY KEY, object_id VARCHAR(128) NOT NULL, field_name VARCHAR(128) NOT NULL,
  data_type VARCHAR(64) NOT NULL, nullable BOOLEAN NOT NULL, classification VARCHAR(64) NOT NULL,
  is_time_field BOOLEAN NOT NULL DEFAULT FALSE, catalog_version VARCHAR(64) NOT NULL,
  FOREIGN KEY (object_id) REFERENCES catalog_objects(object_id)
);
CREATE TABLE IF NOT EXISTS metric_definitions (
  metric_id VARCHAR(128) PRIMARY KEY, name VARCHAR(255) NOT NULL, formula TEXT NOT NULL,
  time_field VARCHAR(255) NOT NULL, grain_json JSON NOT NULL, required_filters_json JSON NOT NULL,
  forbidden_join_patterns_json JSON NOT NULL, null_policy VARCHAR(128) NOT NULL,
  rounding VARCHAR(32) NOT NULL, metric_version VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS business_presets (
  preset_id VARCHAR(128) PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT NOT NULL,
  metric_ids_json JSON NOT NULL, object_ids_json JSON NOT NULL, version VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS table_relations (
  relation_id VARCHAR(128) PRIMARY KEY, left_ref VARCHAR(255) NOT NULL, right_ref VARCHAR(255) NOT NULL,
  cardinality VARCHAR(32) NOT NULL, verified BOOLEAN NOT NULL, relation_version VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_id VARCHAR(128) PRIMARY KEY, alias_text VARCHAR(255) NOT NULL, target_type VARCHAR(32) NOT NULL,
  target_id VARCHAR(128) NOT NULL, version VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS permission_policies (
  policy_version VARCHAR(128) PRIMARY KEY, role_name VARCHAR(64) NOT NULL, scope_mode VARCHAR(32) NOT NULL,
  allowed_domains_json JSON NOT NULL, denied_classifications_json JSON NOT NULL, created_at DATETIME NOT NULL
);