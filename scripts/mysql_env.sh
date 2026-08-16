#!/usr/bin/env bash

# Initialize and inspect the local MySQL environment for Data Runtime Agent.
# Local-only credential defaults. Override them with environment variables when needed.

set -Eeuo pipefail

MYSQL_HOST="${MYSQL_HOST:-localhost}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="${MYSQL_DATABASE:-data_agent}"
MYSQL_CHARSET="${MYSQL_CHARSET:-utf8mb4}"
MYSQL_COLLATION="${MYSQL_COLLATION:-utf8mb4_unicode_ci}"
MYSQL_ROOT_USER="${MYSQL_ROOT_USER:-root}"
MYSQL_ACCOUNT_HOST="${MYSQL_ACCOUNT_HOST:-localhost}"

MYSQL_MIGRATION_USER="${MYSQL_MIGRATION_USER:-agent_migration}"
MYSQL_READER_USER="${MYSQL_READER_USER:-agent_reader}"
MYSQL_WRITER_USER="${MYSQL_WRITER_USER:-agent_writer}"

# The current local root password. Change this line if the root password changes.
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-lxh152732}"
MYSQL_MIGRATION_PASSWORD="${MYSQL_MIGRATION_PASSWORD:-123456}"
MYSQL_READER_PASSWORD="${MYSQL_READER_PASSWORD:-123456}"
MYSQL_WRITER_PASSWORD="${MYSQL_WRITER_PASSWORD:-123456}"

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
  scripts/mysql_env.sh check
  scripts/mysql_env.sh grants
  scripts/mysql_env.sh tables
  scripts/mysql_env.sh help

Commands:
  init     Create the database, accounts, and least-privilege grants. Safe to rerun.
  check    Check root access, database existence, account connections, and grants.
  grants   Display grants for the three application accounts.
  tables   List tables currently present in the application database.

Optional environment variables:
  MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_CHARSET, MYSQL_COLLATION
  MYSQL_ROOT_USER, MYSQL_ROOT_PASSWORD, MYSQL_ACCOUNT_HOST
  MYSQL_MIGRATION_USER, MYSQL_MIGRATION_PASSWORD
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
  validate_identifier MYSQL_DATABASE "$MYSQL_DATABASE"
  validate_identifier MYSQL_CHARSET "$MYSQL_CHARSET"
  validate_identifier MYSQL_COLLATION "$MYSQL_COLLATION"
  validate_identifier MYSQL_MIGRATION_USER "$MYSQL_MIGRATION_USER"
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

  if [[ -n "$MYSQL_ROOT_PASSWORD" ]]; then
    # This is convenient for local automation. Avoid exporting this variable
    # globally; passing it only to the child process limits its lifetime.
    ROOT_ARGS+=(--password="$MYSQL_ROOT_PASSWORD")
  else
    ROOT_ARGS+=(--password)
  fi
}

app_mysql_args() {
  local username="$1"
  local password="$2"

  APP_ARGS=(
    --protocol=TCP
    --host="$MYSQL_HOST"
    --port="$MYSQL_PORT"
    --user="$username"
    --password="$password"
    --database="$MYSQL_DATABASE"
  )
}

run_as_root() {
  root_mysql_args
  mysql "${ROOT_ARGS[@]}" "$@"
}

load_application_passwords() {
  read_secret MYSQL_MIGRATION_PASSWORD 'Migration 账号密码'
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
  local reader_password
  local writer_password
  migration_password="$(sql_literal "$MYSQL_MIGRATION_PASSWORD")"
  reader_password="$(sql_literal "$MYSQL_READER_PASSWORD")"
  writer_password="$(sql_literal "$MYSQL_WRITER_PASSWORD")"

  info "连接 MySQL 并创建数据库和账号"
  run_as_root <<SQL
CREATE DATABASE IF NOT EXISTS \`$MYSQL_DATABASE\`
  CHARACTER SET $MYSQL_CHARSET
  COLLATE $MYSQL_COLLATION;

CREATE USER IF NOT EXISTS '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$migration_password';
CREATE USER IF NOT EXISTS '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$reader_password';
CREATE USER IF NOT EXISTS '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$writer_password';

ALTER USER '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$migration_password';
ALTER USER '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$reader_password';
ALTER USER '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST'
  IDENTIFIED BY '$writer_password';

GRANT ALL PRIVILEGES ON \`$MYSQL_DATABASE\`.*
  TO '$MYSQL_MIGRATION_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, SHOW VIEW ON \`$MYSQL_DATABASE\`.*
  TO '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
GRANT SELECT, INSERT, UPDATE ON \`$MYSQL_DATABASE\`.*
  TO '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';

FLUSH PRIVILEGES;
SQL

  info "初始化完成"
  printf '数据库: %s\n' "$MYSQL_DATABASE"
  printf '账号 host: %s\n' "$MYSQL_ACCOUNT_HOST"
  printf '下一步：把本次输入的三个账号密码填入 config.yaml 的 mysql.accounts。\n'
}

check_root_and_database() {
  info "检查 root 连接和数据库"
  run_as_root --batch --skip-column-names <<SQL
SELECT CONCAT('MySQL version: ', VERSION());
SELECT CONCAT('Root user: ', CURRENT_USER());
SELECT CONCAT('Database exists: ', IF(COUNT(*) = 1, 'yes', 'no'))
  FROM INFORMATION_SCHEMA.SCHEMATA
  WHERE SCHEMA_NAME = '$MYSQL_DATABASE';
SQL
}

check_application_account() {
  local label="$1"
  local username="$2"
  local password="$3"

  app_mysql_args "$username" "$password"
  if mysql "${APP_ARGS[@]}" --batch --skip-column-names \
      -e "SELECT CONCAT('$label connected as ', CURRENT_USER(), ', database ', DATABASE());"; then
    return 0
  fi

  warn "$label 连接失败"
  return 1
}

check_environment() {
  local failed=0

  load_root_password
  load_application_passwords
  check_root_and_database || failed=1

  info "检查应用账号连接"
  check_application_account migration "$MYSQL_MIGRATION_USER" "$MYSQL_MIGRATION_PASSWORD" || failed=1
  check_application_account reader "$MYSQL_READER_USER" "$MYSQL_READER_PASSWORD" || failed=1
  check_application_account writer "$MYSQL_WRITER_USER" "$MYSQL_WRITER_PASSWORD" || failed=1

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
SHOW GRANTS FOR '$MYSQL_READER_USER'@'$MYSQL_ACCOUNT_HOST';
SHOW GRANTS FOR '$MYSQL_WRITER_USER'@'$MYSQL_ACCOUNT_HOST';
SQL
}

list_tables() {
  load_root_password
  info "当前数据库表"
  run_as_root --database="$MYSQL_DATABASE" --execute='SHOW TABLES;'
}

main() {
  require_command mysql
  validate_configuration

  case "${1:-help}" in
    init)
      create_environment
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
