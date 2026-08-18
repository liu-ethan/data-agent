#!/usr/bin/env bash

# Initialize and inspect the local MySQL environment for Data Runtime Agent.
# Two databases are owned by this project:
#   data_agent_ecommerce   the business dataset the agent reads from
#   data_agent_system      the application control plane (identity, runtime,
#                          catalog, sessions, memories)
# Credentials are read from environment variables or an interactive prompt.

set -Eeuo pipefail

MYSQL_HOST="${MYSQL_HOST:-localhost}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_BUSINESS_DATABASE="${MYSQL_BUSINESS_DATABASE:-data_agent_ecommerce}"
MYSQL_SYSTEM_DATABASE="${MYSQL_SYSTEM_DATABASE:-data_agent_system}"
MYSQL_CHARSET="${MYSQL_CHARSET:-utf8mb4}"
MYSQL_COLLATION="${MYSQL_COLLATION:-utf8mb4_unicode_ci}"
MYSQL_ROOT_USER="${MYSQL_ROOT_USER:-root}"
MYSQL_ACCOUNT_HOST="${MYSQL_ACCOUNT_HOST:-localhost}"

MYSQL_MIGRATION_USER="${MYSQL_MIGRATION_USER:-agent_migration}"
MYSQL_CONTROL_USER="${MYSQL_CONTROL_USER:-agent_control}"
MYSQL_READER_USER="${MYSQL_READER_USER:-agent_reader}"
MYSQL_WRITER_USER="${MYSQL_WRITER_USER:-agent_writer}"

MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-}"
MYSQL_MIGRATION_PASSWORD="${MYSQL_MIGRATION_PASSWORD:-}"
MYSQL_CONTROL_PASSWORD="${MYSQL_CONTROL_PASSWORD:-}"
MYSQL_READER_PASSWORD="${MYSQL_READER_PASSWORD:-}"
MYSQL_WRITER_PASSWORD="${MYSQL_WRITER_PASSWORD:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

usage() {
  cat <<'EOF'
Usage:
  scripts/mysql_env.sh init
  scripts/mysql_env.sh harden
  scripts/mysql_env.sh check
  scripts/mysql_env.sh grants
  scripts/mysql_env.sh tables
  scripts/mysql_env.sh help

Commands:
  init     Create both databases and accounts; only migration gets privileges.
  harden   Apply least-privilege grants after schemas and migrations exist.
  check    Check root access, both databases, account connections and grants.
  grants   Display grants for the four application accounts.
  tables   List tables currently present in both application databases.

Two databases are configured independently:
  MYSQL_BUSINESS_DATABASE  default: data_agent_ecommerce   (read-only gateway target)
  MYSQL_SYSTEM_DATABASE    default: data_agent_system      (control-plane records)

Optional environment variables:
  MYSQL_HOST, MYSQL_PORT, MYSQL_CHARSET, MYSQL_COLLATION
  MYSQL_ROOT_USER, MYSQL_ROOT_PASSWORD, MYSQL_ACCOUNT_HOST
  MYSQL_MIGRATION_USER, MYSQL_MIGRATION_PASSWORD
  MYSQL_CONTROL_USER, MYSQL_CONTROL_PASSWORD
  MYSQL_READER_USER, MYSQL_READER_PASSWORD
  MYSQL_WRITER_USER, MYSQL_WRITER_PASSWORD

Examples:
  scripts/mysql_env.sh init
  MYSQL_HOST=127.0.0.1 scripts/mysql_env.sh check
  MYSQL_MIGRATION_PASSWORD='...' MYSQL_READER_PASSWORD='...' \
    MYSQL_WRITER_PASSWORD='...' scripts/mysql_env.sh init
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "找不到命令 '$1'，请先安装 MySQL 客户端。"
}

validate_identifier() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9_]+$ ]] || die "$name 只能包含字母、数字和下划线：$value"
}

validate_account_host() {
  [[ "$MYSQL_ACCOUNT_HOST" =~ ^[A-Za-z0-9_.%:-]+$ ]] || \
    die "MYSQL_ACCOUNT_HOST 包含不支持的字符：$MYSQL_ACCOUNT_HOST"
}

sql_literal() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\'\'}"
  printf '%s' "$value"
}

read_secret() {
  local variable_name="$1"
  local prompt="$2"
  local current_value="${!variable_name:-}"

  if [[ -n "$current_value" ]]; then
    return
  fi

  [[ -t 0 || -t 1 ]] || die "$variable_name 未设置，且当前不是交互式终端。请通过环境变量提供它。"
  read -r -s -p "$prompt: " current_value
  printf '\n'
  [[ -n "$current_value" ]] || die "$variable_name 不能为空。"
  printf -v "$variable_name" '%s' "$current_value"
}

validate_configuration() {
  [[ "$MYSQL_BUSINESS_DATABASE" != "$MYSQL_SYSTEM_DATABASE" ]] || \
    die "MYSQL_BUSINESS_DATABASE 与 MYSQL_SYSTEM_DATABASE 必须不同"
  validate_identifier MYSQL_BUSINESS_DATABASE "$MYSQL_BUSINESS_DATABASE"
  validate_identifier MYSQL_SYSTEM_DATABASE "$MYSQL_SYSTEM_DATABASE"
  validate_identifier MYSQL_CHARSET "$MYSQL_CHARSET"
  validate_identifier MYSQL_COLLATION "$MYSQL_COLLATION"
  validate_identifier MYSQL_MIGRATION_USER "$MYSQL_MIGRATION_USER"
  validate_identifier MYSQL_CONTROL_USER "$MYSQL_CONTROL_USER"
  validate_identifier MYSQL_READER_USER "$MYSQL_READER_USER"
  validate_identifier MYSQL_WRITER_USER "$MYSQL_WRITER_USER"
  validate_account_host
  [[ "$MYSQL_PORT" =~ ^[0-9]+$ ]] || die "MYSQL_PORT 必须是数字：$MYSQL_PORT"
}

root_mysql_args() {
  ROOT_ARGS=(--user="$MYSQL_ROOT_USER")

  if [[ "$MYSQL_HOST" != "localhost" ]]; then
    ROOT_ARGS+=(--host="$MYSQL_HOST" --port="$MYSQL_PORT")
  fi

}

app_mysql_args() {
  local username="$1"
  local password="$2"
  local database="$3"

  APP_ARGS=(
    --protocol=TCP
    --host="$MYSQL_HOST"
    --port="$MYSQL_PORT"
    --user="$username"
    --database="$database"
  )
  APP_PASSWORD="$password"
}

run_as_root() {
  root_mysql_args
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql "${ROOT_ARGS[@]}" "$@"
}

load_application_passwords() {
  read_secret MYSQL_MIGRATION_PASSWORD 'Migration 账号密码'
  read_secret MYSQL_CONTROL_PASSWORD 'Control 账号密码'
  read_secret MYSQL_READER_PASSWORD 'Reader 账号密码'
  read_secret MYSQL_WRITER_PASSWORD 'Writer 账号密码'
}

load_root_password() {
  read_secret MYSQL_ROOT_PASSWORD 'MySQL root 密码'
}

create_environment() {
  load_root_password
  load_application_passwords

  local migration_password
  local control_password
  local reader_password
  local writer_password
  migration_password="$(sql_literal "$MYSQL_MIGRATION_PASSWORD")"
  control_password="$(sql_literal "$MYSQL_CONTROL_PASSWORD")"
  reader_password="$(sql_literal "$MYSQL_READER_PASSWORD")"
  writer_password="$(sql_literal "$MYSQL_WRITER_PASSWORD")"

  info "连接 MySQL 并创建两个数据库和四个账号"
  run_as_root <<SQL
CREATE DATABASE IF NOT EXISTS \`$MYSQL_BUSINESS_DATABASE\`
  CHARACTER SET $MYSQL_CHARSET
  COLLATE $MYSQL_COLLATION;
CREATE DATABASE IF NOT EXISTS \`$MYSQL_SYSTEM_DATABASE\`
  CHARACTER SET $MYSQL_CHARSET
  COLLATE $MYSQL_COLLATION;

CREATE USER IF NOT EXISTS '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$migration_password';
CREATE USER IF NOT EXISTS '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$control_password';
CREATE USER IF NOT EXISTS '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$reader_password';
CREATE USER IF NOT EXISTS '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$writer_password';

ALTER USER '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$migration_password';
ALTER USER '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$control_password';
ALTER USER '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$reader_password';
ALTER USER '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$writer_password';

GRANT ALL PRIVILEGES ON \`$MYSQL_BUSINESS_DATABASE\`.*
  TO '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT ALL PRIVILEGES ON \`$MYSQL_SYSTEM_DATABASE\`.*
  TO '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';

FLUSH PRIVILEGES;
SQL

  info "初始化完成"
  printf '业务库: %s\n' "$MYSQL_BUSINESS_DATABASE"
  printf '系统库: %s\n' "$MYSQL_SYSTEM_DATABASE"
  printf '账号 host: %s\n' "$MYSQL_ACCOUNT_HOST"
  printf '下一步：\n'
  printf '  1. 在业务库执行 scripts/business_schema.sql\n'
  printf '  2. 在系统库依次执行 migrations/00?_system_*.sql\n'
  printf '  3. 运行 scripts/mysql_env.sh harden 应用最小权限授权\n'
  printf '  4. 在业务库运行 scripts/mock_mysql_data.sh seed\n'
  printf '  5. 在系统库运行 scripts/runtime_seed.sql 与 catalog_seed.sql\n'
}

harden_grants() {
  load_root_password
  info "应用最小权限账号授权"
  run_as_root <<SQL
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';

-- Reader: read-only access to the eight business tables only.
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.shops TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.users TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.categories TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.products TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.orders TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.order_items TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.refunds TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_BUSINESS_DATABASE\`.refund_items TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';

-- Writer: narrow write path on the products table only.
GRANT SELECT ON \`$MYSQL_BUSINESS_DATABASE\`.products TO '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT UPDATE (product_name) ON \`$MYSQL_BUSINESS_DATABASE\`.products TO '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';

-- Control: every control-plane table in the system database.
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.app_users TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.app_user_shop_scopes TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.runtime_checkpoints TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.runtime_checkpoint_history TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.runtime_idempotency TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.runtime_results TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.runtime_events TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.conversation_messages TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.conversation_artifacts TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.user_memories TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.mutation_audit TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE ON \`$MYSQL_SYSTEM_DATABASE\`.invite_codes TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`$MYSQL_SYSTEM_DATABASE\`.thread_titles TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT ON \`$MYSQL_SYSTEM_DATABASE\`.user_memory_history TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';

GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_sources TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_objects TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_fields TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.metric_definitions TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.business_presets TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.table_relations TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.entity_aliases TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.permission_policies TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_object_metadata TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_field_metadata TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_metric_sources TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_relation_sources TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_search_documents TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_search_terms TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_index_manifests TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT ON \`$MYSQL_SYSTEM_DATABASE\`.catalog_snapshots TO '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
FLUSH PRIVILEGES;
SQL
}

check_root_and_databases() {
  info "检查 root 连接和两个数据库"
  run_as_root --batch --skip-column-names <<SQL
SELECT CONCAT('MySQL version: ', VERSION());
SELECT CONCAT('Root user: ', CURRENT_USER());
SELECT CONCAT('Business database exists: ', IF(COUNT(*) = 1, 'yes', 'no'))
  FROM INFORMATION_SCHEMA.SCHEMATA
  WHERE SCHEMA_NAME = '$MYSQL_BUSINESS_DATABASE';
SELECT CONCAT('System database exists: ', IF(COUNT(*) = 1, 'yes', 'no'))
  FROM INFORMATION_SCHEMA.SCHEMATA
  WHERE SCHEMA_NAME = '$MYSQL_SYSTEM_DATABASE';
SQL
}

check_application_account() {
  local label="$1"
  local username="$2"
  local password="$3"
  local database="$4"
  local mode="${5:-system}" # system or business

  app_mysql_args "$username" "$password" "$database"
  local args=("${APP_ARGS[@]}")
  if [[ "$mode" == "expect_failure" ]]; then
    if MYSQL_PWD="$APP_PASSWORD" mysql "${args[@]}" --batch --skip-column-names \
        -e "SELECT 1" >/dev/null 2>&1; then
      warn "$label 不应能访问 $database"
      return 1
    fi
    printf '  %s cannot access %s: ok\n' "$label" "$database"
    return 0
  fi
  if MYSQL_PWD="$APP_PASSWORD" mysql "${args[@]}" --batch --skip-column-names \
      -e "SELECT CONCAT('$label connected as ', CURRENT_USER(), ', database ', DATABASE());"; then
    return 0
  fi

  warn "$label 连接 $database 失败"
  return 1
}

check_environment() {
  local failed=0

  load_root_password
  load_application_passwords
  check_root_and_databases || failed=1

  info "检查应用账号连接 (业务库 / 系统库)"
  check_application_account migration "$MYSQL_MIGRATION_USER" "$MYSQL_MIGRATION_PASSWORD" "$MYSQL_BUSINESS_DATABASE" || failed=1
  check_application_account migration "$MYSQL_MIGRATION_USER" "$MYSQL_MIGRATION_PASSWORD" "$MYSQL_SYSTEM_DATABASE" || failed=1
  check_application_account control "$MYSQL_CONTROL_USER" "$MYSQL_CONTROL_PASSWORD" "$MYSQL_SYSTEM_DATABASE" || failed=1
  check_application_account reader "$MYSQL_READER_USER" "$MYSQL_READER_PASSWORD" "$MYSQL_BUSINESS_DATABASE" || failed=1

  info "检查隔离策略 (reader 不可访问系统库, control 不可访问业务库)"
  check_application_account reader "$MYSQL_READER_USER" "$MYSQL_READER_PASSWORD" "$MYSQL_SYSTEM_DATABASE" expect_failure || failed=1
  check_application_account control "$MYSQL_CONTROL_USER" "$MYSQL_CONTROL_PASSWORD" "$MYSQL_BUSINESS_DATABASE" expect_failure || failed=1

  show_grants || failed=1
  list_tables || failed=1

  if (( failed != 0 )); then
    die "检查未通过，请根据上面的输出排查。"
  fi
  info "检查通过"
}

show_grants() {
  load_root_password
  info "应用账号权限"
  run_as_root <<SQL
SHOW GRANTS FOR '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST';
SHOW GRANTS FOR '$MYSQL_CONTROL_USER'@'$MYSQL_ACCOUNT_HOST';
SHOW GRANTS FOR '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
SHOW GRANTS FOR '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';
SQL
}

list_tables() {
  load_root_password
  info "业务库 (${MYSQL_BUSINESS_DATABASE}) 当前表"
  run_as_root --database="$MYSQL_BUSINESS_DATABASE" --execute='SHOW TABLES;'
  info "系统库 (${MYSQL_SYSTEM_DATABASE}) 当前表"
  run_as_root --database="$MYSQL_SYSTEM_DATABASE" --execute='SHOW TABLES;'
}

main() {
  require_command mysql
  validate_configuration

  case "${1:-help}" in
    init)
      create_environment
      ;;
    harden)
      harden_grants
      ;;
    check|status)
      check_environment
      ;;
    grants)
      show_grants
      ;;
    tables)
      list_tables
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage >&2
      die "未知命令：$1"
      ;;
  esac
}

main "$@"