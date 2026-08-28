#!/usr/bin/env bash
# 需要 MySQL DDL 账号（root）。reader/writer 不能建表。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MYSQL_OPTS=(-h localhost -P 3306 -u root -p --default-character-set=utf8mb4)
mysql "${MYSQL_OPTS[@]}" < "$ROOT/migrations/mysql/001_ecommerce_slice.sql"
mysql "${MYSQL_OPTS[@]}" < "$ROOT/migrations/mysql/002_ecommerce_seed.sql"
mysql "${MYSQL_OPTS[@]}" < "$ROOT/migrations/mysql/003_tighten_writer_grants.sql"
echo "MySQL ecommerce slice applied."
