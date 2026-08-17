#!/usr/bin/env bash

# One-shot setup for both Data Runtime Agent databases.
#
#   data_agent_ecommerce   the eight Spec 01 business tables (reader target)
#   data_agent_system      the application control plane (identity, runtime,
#                          catalog, sessions, memories)
#
# The script is idempotent: re-running it on a machine where the databases
# already exist leaves the schema and seeds intact. Use this when bringing up
# a fresh machine or after wiping the local MySQL container.
#
# Required environment variables:
#   MYSQL_ROOT_PASSWORD
#   MYSQL_MIGRATION_PASSWORD
#   MYSQL_CONTROL_PASSWORD
#   MYSQL_READER_PASSWORD
#   MYSQL_WRITER_PASSWORD
#
# Optional overrides:
#   MYSQL_HOST                default: localhost
#   MYSQL_PORT                default: 3306
#   MYSQL_BUSINESS_DATABASE   default: data_agent_ecommerce
#   MYSQL_SYSTEM_DATABASE     default: data_agent_system
#   MYSQL_MIGRATION_USER      default: agent_migration
#   MYSQL_CONTROL_USER        default: agent_control
#   MYSQL_READER_USER         default: agent_reader
#   MYSQL_WRITER_USER         default: agent_writer
#   SKIP_HARDEN=1             skip least-privilege grant application
#   SKIP_SEED=1               skip the business / catalog / identity seeds

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MYSQL_HOST="${MYSQL_HOST:-localhost}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_BUSINESS_DATABASE="${MYSQL_BUSINESS_DATABASE:-data_agent_ecommerce}"
MYSQL_SYSTEM_DATABASE="${MYSQL_SYSTEM_DATABASE:-data_agent_system}"
MYSQL_MIGRATION_USER="${MYSQL_MIGRATION_USER:-agent_migration}"
MYSQL_CONTROL_USER="${MYSQL_CONTROL_USER:-agent_control}"
MYSQL_READER_USER="${MYSQL_READER_USER:-agent_reader}"
MYSQL_WRITER_USER="${MYSQL_WRITER_USER:-agent_writer}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "请设置环境变量 $name（或在交互式终端输入）"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "找不到命令 '$1'，请先安装 MySQL 客户端。"
}

[[ "$MYSQL_BUSINESS_DATABASE" != "$MYSQL_SYSTEM_DATABASE" ]] || \
  die "MYSQL_BUSINESS_DATABASE 与 MYSQL_SYSTEM_DATABASE 必须不同"

require_command mysql

for var in MYSQL_ROOT_PASSWORD MYSQL_MIGRATION_PASSWORD MYSQL_CONTROL_PASSWORD \
           MYSQL_READER_PASSWORD MYSQL_WRITER_PASSWORD; do
  require_env "$var"
done

cd "${PROJECT_ROOT}"

info "1/5 创建两个数据库 + 四个账号 (root)"
MYSQL_HOST="$MYSQL_HOST" MYSQL_PORT="$MYSQL_PORT" MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" \
MYSQL_BUSINESS_DATABASE="$MYSQL_BUSINESS_DATABASE" MYSQL_SYSTEM_DATABASE="$MYSQL_SYSTEM_DATABASE" \
MYSQL_MIGRATION_USER="$MYSQL_MIGRATION_USER" MYSQL_CONTROL_USER="$MYSQL_CONTROL_USER" \
MYSQL_READER_USER="$MYSQL_READER_USER" MYSQL_WRITER_USER="$MYSQL_WRITER_USER" \
  bash scripts/mysql_env.sh init

info "2/5 应用业务库 DDL (${MYSQL_BUSINESS_DATABASE})"
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" \
  --user=root --database="$MYSQL_BUSINESS_DATABASE" < scripts/business_schema.sql

info "3/5 应用系统库迁移 (${MYSQL_SYSTEM_DATABASE})"
for migration in migrations/00?_system_*.sql; do
  printf '  -> %s\n' "$migration"
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" \
    --user=root --database="$MYSQL_SYSTEM_DATABASE" < "$migration"
done

if [[ -z "${SKIP_HARDEN:-}" ]]; then
  info "4/5 应用最小权限授权"
  MYSQL_HOST="$MYSQL_HOST" MYSQL_PORT="$MYSQL_PORT" MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" \
  MYSQL_BUSINESS_DATABASE="$MYSQL_BUSINESS_DATABASE" MYSQL_SYSTEM_DATABASE="$MYSQL_SYSTEM_DATABASE" \
  MYSQL_MIGRATION_USER="$MYSQL_MIGRATION_USER" MYSQL_CONTROL_USER="$MYSQL_CONTROL_USER" \
  MYSQL_READER_USER="$MYSQL_READER_USER" MYSQL_WRITER_USER="$MYSQL_WRITER_USER" \
    bash scripts/mysql_env.sh harden
else
  info "4/5 跳过最小权限授权 (SKIP_HARDEN=1)"
fi

if [[ -z "${SKIP_SEED:-}" ]]; then
  info "5/5 写入种子数据 (业务库 seed_v1 + 系统库 identities/catalog)"
  MYSQL_DATABASE="$MYSQL_BUSINESS_DATABASE" MYSQL_HOST="$MYSQL_HOST" MYSQL_PORT="$MYSQL_PORT" \
  MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" MYSQL_USER=root \
    bash scripts/mock_mysql_data.sh seed

  MYSQL_DATABASE="$MYSQL_SYSTEM_DATABASE" MYSQL_HOST="$MYSQL_HOST" MYSQL_PORT="$MYSQL_PORT" \
  MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" MYSQL_USER=root \
    bash scripts/mock_mysql_data.sh check >/dev/null 2>&1 || true
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" \
    --user=root --database="$MYSQL_SYSTEM_DATABASE" < scripts/runtime_seed.sql
  MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" \
    --user=root --database="$MYSQL_SYSTEM_DATABASE" < scripts/catalog_seed.sql
else
  info "5/5 跳过种子写入 (SKIP_SEED=1)"
fi

info "完成。两个库当前表:"
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --host="$MYSQL_HOST" --port="$MYSQL_PORT" \
  --user=root -e "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ('$MYSQL_BUSINESS_DATABASE', '$MYSQL_SYSTEM_DATABASE') ORDER BY TABLE_SCHEMA, TABLE_NAME;"

printf '\n业务库: %s\n' "$MYSQL_BUSINESS_DATABASE"
printf '系统库: %s\n' "$MYSQL_SYSTEM_DATABASE"