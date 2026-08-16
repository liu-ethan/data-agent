-- Password verifiers are provisioned separately from identity/permission seeds.
-- The conditional statement keeps a fresh 002 -> 007 migration sequence idempotent.
SET @password_hash_exists = (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'app_users'
    AND COLUMN_NAME = 'password_hash'
);
SET @password_auth_migration = IF(
  @password_hash_exists = 0,
  'ALTER TABLE app_users ADD COLUMN password_hash VARCHAR(255) NULL AFTER user_id',
  'SELECT 1'
);
PREPARE password_auth_statement FROM @password_auth_migration;
EXECUTE password_auth_statement;
DEALLOCATE PREPARE password_auth_statement;
