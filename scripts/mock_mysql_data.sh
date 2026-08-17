#!/usr/bin/env bash

# Seed the deterministic ecommerce dataset required by Spec 01.
# Targets the business database (data_agent_ecommerce by default); system
# tables (catalog, identity, runtime, sessions, memories) live in
# data_agent_system and are not touched by this script.
# This script only validates existing tables and writes rows; it never creates
# or alters tables.

set -Eeuo pipefail

MYSQL_HOST="${MYSQL_HOST:-localhost}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="${MYSQL_DATABASE:-data_agent_ecommerce}"
MYSQL_USER="${MYSQL_USER:-${MYSQL_ROOT_USER:-root}}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"

SEED_VERSION="seed_v1"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '\n==> %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage:
  scripts/mock_mysql_data.sh check
  scripts/mock_mysql_data.sh seed
  scripts/mock_mysql_data.sh help

Commands:
  check    Validate the eight Spec 01 tables and their required columns.
  seed     Validate the schema, then upsert the deterministic seed_v1 rows.
  help     Show this message.

The seed is repeatable. Rows use stable IDs and are upserted, so rerunning the
command updates the seed rows without accumulating duplicates. Existing data
with other IDs is not deleted.

Connection environment variables:
  MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE
  MYSQL_USER, MYSQL_PASSWORD
  MYSQL_ROOT_USER, MYSQL_ROOT_PASSWORD (legacy aliases)

Example:
  MYSQL_ROOT_PASSWORD='...' MYSQL_DATABASE=data_agent_ecommerce \
    scripts/mock_mysql_data.sh seed
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "找不到命令 '$1'，请先安装 MySQL 客户端。"
}

validate_configuration() {
  [[ "$MYSQL_DATABASE" =~ ^[A-Za-z0-9_]+$ ]] || \
    die "MYSQL_DATABASE 只能包含字母、数字和下划线：$MYSQL_DATABASE"
  [[ "$MYSQL_PORT" =~ ^[0-9]+$ ]] || die "MYSQL_PORT 必须是数字：$MYSQL_PORT"
}

mysql_args() {
  MYSQL_ARGS=(
    --protocol=TCP
    --host="$MYSQL_HOST"
    --port="$MYSQL_PORT"
    --user="$MYSQL_USER"
    --database="$MYSQL_DATABASE"
  )

}

run_mysql() {
  mysql_args
  if [[ -n "$MYSQL_PASSWORD" ]]; then
    MYSQL_PWD="$MYSQL_PASSWORD" mysql "${MYSQL_ARGS[@]}" "$@"
  else
    mysql "${MYSQL_ARGS[@]}" --password "$@"
  fi
}

query_mysql() {
  run_mysql --batch --skip-column-names --raw -e "$1"
}

has_table() {
  [[ "$(query_mysql "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '$1';")" == "1" ]]
}

has_column() {
  [[ "$(query_mysql "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '$1' AND COLUMN_NAME = '$2';")" == "1" ]]
}

require_table() {
  local table="$1"
  if ! has_table "$table"; then
    printf '  missing table: %s\n' "$table" >&2
    return 1
  fi
}

require_column() {
  local table="$1"
  local column="$2"
  if ! has_column "$table" "$column"; then
    printf '  missing column: %s.%s\n' "$table" "$column" >&2
    return 1
  fi
}

first_existing_column() {
  local table="$1"
  shift
  local column

  for column in "$@"; do
    if has_column "$table" "$column"; then
      printf '%s' "$column"
      return 0
    fi
  done
  return 1
}

SCHEMA_ERROR_COUNT=0

check_schema() {
  local table
  local column

  info "校验 Spec 01 业务表"
  for table in shops users categories products orders order_items refunds refund_items; do
    require_table "$table" || SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  done

  local -a required_columns=(
    'shops:shop_id shop_name region_code region_name status'
    'users:phone id_number created_at'
    'categories:category_id parent_id category_name'
    'products:product_id shop_id category_id product_name status'
    'orders:order_id shop_id status paid_at pay_amount created_at'
    'order_items:shop_id order_id product_id quantity item_paid_amount'
    'refunds:refund_id order_id shop_id refund_amount refunded_at'
    'refund_items:refund_item_id refund_id shop_id order_item_id'
  )

  local entry columns
  for entry in "${required_columns[@]}"; do
    table="${entry%%:*}"
    columns="${entry#*:}"
    # Avoid cascading column errors when the table itself is absent.
    has_table "$table" || continue
    for column in $columns; do
      require_column "$table" "$column" || SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
    done
  done

  # These aliases appeared in earlier architecture drafts. They are accepted
  # only as explicit compatibility variants, never as arbitrary column names.
  if has_table users && ! first_existing_column users user_id buyer_id >/dev/null; then
    printf '  missing column: users.user_id (兼容旧字段 users.buyer_id 也可)\n' >&2
    SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  fi
  if has_table orders && ! first_existing_column orders user_id buyer_id >/dev/null; then
    printf '  missing column: orders.user_id (兼容旧字段 orders.buyer_id 也可)\n' >&2
    SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  fi
  if has_table order_items && ! first_existing_column order_items item_id order_item_id >/dev/null; then
    printf '  missing column: order_items.item_id (兼容旧字段 order_item_id 也可)\n' >&2
    SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  fi
  if has_table refunds && ! first_existing_column refunds status refund_status >/dev/null; then
    printf '  missing column: refunds.status (兼容旧字段 refund_status 也可)\n' >&2
    SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  fi
  if has_table refund_items && ! first_existing_column refund_items refund_amount refund_item_amount >/dev/null; then
    printf '  missing column: refund_items.refund_amount (兼容旧字段 refund_item_amount 也可)\n' >&2
    SCHEMA_ERROR_COUNT=$((SCHEMA_ERROR_COUNT + 1))
  fi

  if (( SCHEMA_ERROR_COUNT != 0 )); then
    die "表结构校验失败，共 $SCHEMA_ERROR_COUNT 项缺失。脚本不会写入数据。"
  fi

  USER_ID_COLUMN="$(first_existing_column users user_id buyer_id)"
  ORDER_BUYER_COLUMN="$(first_existing_column orders user_id buyer_id)"
  ORDER_ITEM_ID_COLUMN="$(first_existing_column order_items item_id order_item_id)"
  REFUND_STATUS_COLUMN="$(first_existing_column refunds status refund_status)"
  REFUND_ITEM_AMOUNT_COLUMN="$(first_existing_column refund_items refund_amount refund_item_amount)"

  info "表结构校验通过"
  printf '  users id column: %s\n' "$USER_ID_COLUMN"
  printf '  orders buyer column: %s\n' "$ORDER_BUYER_COLUMN"
  printf '  order_items id column: %s\n' "$ORDER_ITEM_ID_COLUMN"
  printf '  refunds status column: %s\n' "$REFUND_STATUS_COLUMN"
  printf '  refund_items amount column: %s\n' "$REFUND_ITEM_AMOUNT_COLUMN"
}

build_compatibility_sql() {
  ORDER_ITEM_COLUMNS="\`$ORDER_ITEM_ID_COLUMN\`, \`order_id\`, \`product_id\`, \`quantity\`, \`item_paid_amount\`"
  ORDER_ITEM_VALUES="$(cat <<'EOF'
('item_001', 'ord_001', 'prod_1001', 1, 899.00),
('item_002', 'ord_001', 'prod_1002', 1, 49.00),
('item_003', 'ord_001', 'prod_1003', 1, 351.00),
('item_004', 'ord_002', 'prod_1003', 1, 351.00),
('item_005', 'ord_002', 'prod_1002', 1, 49.00),
('item_006', 'ord_002', 'prod_1004', 1, 100.00),
('item_007', 'ord_003', 'prod_2001', 1, 500.00),
('item_008', 'ord_003', 'prod_2002', 1, 280.00),
('item_009', 'ord_004', 'prod_3001', 1, 180.00),
('item_010', 'ord_004', 'prod_3002', 1, 140.00),
('item_011', 'ord_005', 'prod_1001', 1, 899.00),
('item_012', 'ord_010', 'prod_1004', 1, 700.00)
EOF
)"

  REFUND_COLUMNS="\`refund_id\`, \`order_id\`, \`$REFUND_STATUS_COLUMN\`, \`refund_amount\`, \`refunded_at\`"
  REFUND_VALUES="$(cat <<EOF
('refund_001', 'ord_001', 'SUCCESS', 100.00, '${YESTERDAY} 18:00:00'),
('refund_002', 'ord_001', 'SUCCESS', 50.00, '${TWO_DAYS_AGO} 09:00:00'),
('refund_003', 'ord_003', 'PENDING', 280.00, NULL),
('refund_004', 'ord_002', 'FAILED', 49.00, '${YESTERDAY} 12:00:00')
EOF
)"
  REFUND_ITEM_COLUMNS="\`refund_item_id\`, \`refund_id\`, \`order_item_id\`, \`$REFUND_ITEM_AMOUNT_COLUMN\`"
  REFUND_ITEM_VALUES="$(cat <<'EOF'
('refund_item_001', 'refund_001', 'item_001', 100.00),
('refund_item_002', 'refund_002', 'item_003', 50.00),
('refund_item_003', 'refund_003', 'item_008', 280.00),
('refund_item_004', 'refund_004', 'item_005', 49.00)
EOF
)"

  # The optional shop_id columns are present in the architecture table sketch
  # but not required by Spec 01. Include them when the existing schema has them.
  if has_column order_items shop_id; then
    ORDER_ITEM_COLUMNS="\`$ORDER_ITEM_ID_COLUMN\`, \`order_id\`, \`shop_id\`, \`product_id\`, \`quantity\`, \`item_paid_amount\`"
    ORDER_ITEM_VALUES="$(cat <<'EOF'
('item_001', 'ord_001', 'shop_001', 'prod_1001', 1, 899.00),
('item_002', 'ord_001', 'shop_001', 'prod_1002', 1, 49.00),
('item_003', 'ord_001', 'shop_001', 'prod_1003', 1, 351.00),
('item_004', 'ord_002', 'shop_001', 'prod_1003', 1, 351.00),
('item_005', 'ord_002', 'shop_001', 'prod_1002', 1, 49.00),
('item_006', 'ord_002', 'shop_001', 'prod_1004', 1, 100.00),
('item_007', 'ord_003', 'shop_002', 'prod_2001', 1, 500.00),
('item_008', 'ord_003', 'shop_002', 'prod_2002', 1, 280.00),
('item_009', 'ord_004', 'shop_002', 'prod_3001', 1, 180.00),
('item_010', 'ord_004', 'shop_002', 'prod_3002', 1, 140.00),
('item_011', 'ord_005', 'shop_001', 'prod_1001', 1, 899.00),
('item_012', 'ord_010', 'shop_001', 'prod_1004', 1, 700.00)
EOF
)"
  fi

  if has_column refunds shop_id; then
    REFUND_COLUMNS="\`refund_id\`, \`order_id\`, \`shop_id\`, \`$REFUND_STATUS_COLUMN\`, \`refund_amount\`, \`refunded_at\`"
    REFUND_VALUES="$(cat <<'EOF'
('refund_001', 'ord_001', 'shop_001', 'SUCCESS', 100.00, '2026-08-15 18:00:00'),
('refund_002', 'ord_001', 'shop_001', 'SUCCESS', 50.00, '2026-08-16 09:00:00'),
('refund_003', 'ord_003', 'shop_002', 'PENDING', 280.00, NULL),
('refund_004', 'ord_002', 'shop_001', 'FAILED', 49.00, '2026-08-15 12:00:00')
EOF
)"
  fi

  if has_column refund_items shop_id; then
    REFUND_ITEM_COLUMNS="\`refund_item_id\`, \`refund_id\`, \`order_item_id\`, \`shop_id\`, \`$REFUND_ITEM_AMOUNT_COLUMN\`"
    REFUND_ITEM_VALUES="$(cat <<'EOF'
('refund_item_001', 'refund_001', 'item_001', 'shop_001', 100.00),
('refund_item_002', 'refund_002', 'item_003', 'shop_001', 50.00),
('refund_item_003', 'refund_003', 'item_008', 'shop_002', 280.00),
('refund_item_004', 'refund_004', 'item_005', 'shop_001', 49.00)
EOF
)"
  fi
}

seed_data() {
  # Order dates are anchored to the runtime clock so the deterministic
  # eval suite (which queries "昨天") keeps matching whichever calendar
  # day the seed runs on. The runtime reads Asia/Shanghai; the MySQL
  # session uses the OS-local timezone, which is also Asia/Shanghai in
  # our dev container.
  YESTERDAY=$(date -d 'yesterday' '+%Y-%m-%d')
  TWO_DAYS_AGO=$(date -d '2 days ago' '+%Y-%m-%d')
  THREE_DAYS_AGO=$(date -d '3 days ago' '+%Y-%m-%d')
  SEVEN_DAYS_AGO=$(date -d '7 days ago' '+%Y-%m-%d')
  TODAY=$(date '+%Y-%m-%d')

  build_compatibility_sql
  info "写入 $SEED_VERSION"

  # The heredoc is quoted ('SQL') so the embedded backticks stay as
  # MySQL identifier quotes; the date placeholders are substituted by
  # sed after the heredoc is read.
  HEREDOC_SQL="$(cat <<'SQL'
SET NAMES utf8mb4;
START TRANSACTION;

INSERT INTO `shops` (`shop_id`, `shop_name`, `region_code`, `region_name`, `status`) VALUES
  ('shop_001', '东城数码旗舰店', 'CN-EAST', '华东', 'ACTIVE'),
  ('shop_002', '华南生活馆', 'CN-SOUTH', '华南地区', 'ACTIVE'),
  ('shop_003', '西部家居店', 'CN-WEST', '西部', 'INACTIVE')
ON DUPLICATE KEY UPDATE
  `shop_name` = VALUES(`shop_name`), `region_code` = VALUES(`region_code`),
  `region_name` = VALUES(`region_name`), `status` = VALUES(`status`);

INSERT INTO `users` (`$USER_ID_COLUMN`, `phone`, `id_number`, `created_at`) VALUES
  ('user_001', '13800000001', '110101199001011234', '2026-07-01 09:00:00'),
  ('user_002', '13800000002', '310101199202022345', '2026-07-03 10:30:00'),
  ('user_003', '13900000003', '440101198803033456', '2026-07-05 14:20:00'),
  ('user_004', '13900000004', '510101199504044567', '2026-07-08 16:45:00')
ON DUPLICATE KEY UPDATE
  `$USER_ID_COLUMN` = VALUES(`$USER_ID_COLUMN`),
  `phone` = VALUES(`phone`), `id_number` = VALUES(`id_number`),
  `created_at` = VALUES(`created_at`);

INSERT INTO `categories` (`category_id`, `parent_id`, `category_name`) VALUES
  ('cat_100', NULL, '数码'),
  ('cat_110', 'cat_100', '手机通讯'),
  ('cat_120', 'cat_100', '电脑周边'),
  ('cat_200', NULL, '家居'),
  ('cat_210', 'cat_200', '厨房用品'),
  ('cat_300', NULL, '美妆'),
  ('cat_310', 'cat_300', '护肤')
ON DUPLICATE KEY UPDATE
  `parent_id` = VALUES(`parent_id`), `category_name` = VALUES(`category_name`);

INSERT INTO `products` (`product_id`, `shop_id`, `category_id`, `product_name`, `status`) VALUES
  ('prod_1001', 'shop_001', 'cat_110', '智能手机', 'ACTIVE'),
  ('prod_1002', 'shop_001', 'cat_110', '手机保护壳', 'ACTIVE'),
  ('prod_1003', 'shop_001', 'cat_120', '无线耳机', 'ACTIVE'),
  ('prod_1004', 'shop_001', 'cat_120', '快充充电宝', 'ACTIVE'),
  ('prod_2001', 'shop_002', 'cat_210', '不粘炒锅', 'ACTIVE'),
  ('prod_2002', 'shop_002', 'cat_210', '厨房收纳盒', 'ACTIVE'),
  ('prod_3001', 'shop_002', 'cat_310', '保湿面霜', 'ACTIVE'),
  ('prod_3002', 'shop_002', 'cat_310', '修护乳霜', 'ACTIVE'),
  ('prod_3003', 'shop_003', 'cat_310', '旅行护肤套装', 'INACTIVE'),
  ('prod_2003', 'shop_002', 'cat_120', '无线耳机', 'ACTIVE')
ON DUPLICATE KEY UPDATE
  `shop_id` = VALUES(`shop_id`), `category_id` = VALUES(`category_id`),
  `product_name` = VALUES(`product_name`), `status` = VALUES(`status`);

INSERT INTO `orders` (`order_id`, `$ORDER_BUYER_COLUMN`, `shop_id`, `status`, `paid_at`, `pay_amount`, `created_at`) VALUES
  ('ord_001', 'user_001', 'shop_001', 'PAID', '__YESTERDAY__ 09:10:00', 1299.00, '__YESTERDAY__ 08:50:00'),
  ('ord_002', 'user_002', 'shop_001', 'PAID', '__TWO_DAYS_AGO__ 11:20:00', 500.00, '__TWO_DAYS_AGO__ 11:00:00'),
  ('ord_003', 'user_003', 'shop_002', 'PAID', '__YESTERDAY__ 13:05:00', 780.00, '__YESTERDAY__ 12:40:00'),
  ('ord_004', 'user_001', 'shop_002', 'PAID', '__THREE_DAYS_AGO__ 14:10:00', 320.00, '__THREE_DAYS_AGO__ 13:55:00'),
  ('ord_005', 'user_004', 'shop_001', 'PAID', '__SEVEN_DAYS_AGO__ 10:00:00', 899.00, '__SEVEN_DAYS_AGO__ 09:40:00'),
  ('ord_007', 'user_004', 'shop_002', 'UNPAID', NULL, 560.00, '__YESTERDAY__ 16:00:00'),
  ('ord_008', 'user_003', 'shop_001', 'PAYMENT_FAILED', NULL, 199.00, '__TWO_DAYS_AGO__ 10:00:00'),
  ('ord_009', 'user_002', 'shop_002', 'CANCELLED', NULL, 560.00, '__THREE_DAYS_AGO__ 12:00:00'),
  ('ord_010', 'user_001', 'shop_001', 'PAID', '__TWO_DAYS_AGO__ 08:00:00', 700.00, '__TWO_DAYS_AGO__ 07:40:00')
ON DUPLICATE KEY UPDATE
  `$ORDER_BUYER_COLUMN` = VALUES(`$ORDER_BUYER_COLUMN`), `shop_id` = VALUES(`shop_id`),
  `status` = VALUES(`status`), `paid_at` = VALUES(`paid_at`),
  `pay_amount` = VALUES(`pay_amount`), `created_at` = VALUES(`created_at`);

INSERT INTO `order_items` ($ORDER_ITEM_COLUMNS) VALUES
$ORDER_ITEM_VALUES
ON DUPLICATE KEY UPDATE
  `order_id` = VALUES(`order_id`), `product_id` = VALUES(`product_id`),
  `quantity` = VALUES(`quantity`), `item_paid_amount` = VALUES(`item_paid_amount`);

INSERT INTO `refunds` ($REFUND_COLUMNS) VALUES
$REFUND_VALUES
ON DUPLICATE KEY UPDATE
  `order_id` = VALUES(`order_id`), `$REFUND_STATUS_COLUMN` = VALUES(`$REFUND_STATUS_COLUMN`),
  `refund_amount` = VALUES(`refund_amount`), `refunded_at` = VALUES(`refunded_at`);

INSERT INTO `refund_items` ($REFUND_ITEM_COLUMNS) VALUES
$REFUND_ITEM_VALUES
ON DUPLICATE KEY UPDATE
  `refund_id` = VALUES(`refund_id`), `order_item_id` = VALUES(`order_item_id`),
  `$REFUND_ITEM_AMOUNT_COLUMN` = VALUES(`$REFUND_ITEM_AMOUNT_COLUMN`);

COMMIT;
SQL
)"
  HEREDOC_SQL="${HEREDOC_SQL//__YESTERDAY__/$YESTERDAY}"
  HEREDOC_SQL="${HEREDOC_SQL//__TWO_DAYS_AGO__/$TWO_DAYS_AGO}"
  HEREDOC_SQL="${HEREDOC_SQL//__THREE_DAYS_AGO__/$THREE_DAYS_AGO}"
  HEREDOC_SQL="${HEREDOC_SQL//__SEVEN_DAYS_AGO__/$SEVEN_DAYS_AGO}"
  # Substitute the schema-compatibility column names. The heredoc is
  # quoted so the `$VAR` text below is literal; we replace the whole
  # back-tick-quoted token in one go.
  HEREDOC_SQL="${HEREDOC_SQL//\`\$USER_ID_COLUMN\`/$USER_ID_COLUMN}"
  HEREDOC_SQL="${HEREDOC_SQL//\`\$ORDER_BUYER_COLUMN\`/$ORDER_BUYER_COLUMN}"
  HEREDOC_SQL="${HEREDOC_SQL//\`\$REFUND_STATUS_COLUMN\`/$REFUND_STATUS_COLUMN}"
  HEREDOC_SQL="${HEREDOC_SQL//\`\$REFUND_ITEM_AMOUNT_COLUMN\`/$REFUND_ITEM_AMOUNT_COLUMN}"
  # Substitute the multi-row VALUES blocks captured into shell vars.
  HEREDOC_SQL="${HEREDOC_SQL//\$ORDER_ITEM_COLUMNS/$ORDER_ITEM_COLUMNS}"
  HEREDOC_SQL="${HEREDOC_SQL//\$ORDER_ITEM_VALUES/$ORDER_ITEM_VALUES}"
  HEREDOC_SQL="${HEREDOC_SQL//\$REFUND_COLUMNS/$REFUND_COLUMNS}"
  HEREDOC_SQL="${HEREDOC_SQL//\$REFUND_VALUES/$REFUND_VALUES}"
  HEREDOC_SQL="${HEREDOC_SQL//\$REFUND_ITEM_COLUMNS/$REFUND_ITEM_COLUMNS}"
  HEREDOC_SQL="${HEREDOC_SQL//\$REFUND_ITEM_VALUES/$REFUND_ITEM_VALUES}"
  run_mysql --default-character-set=utf8mb4 <<<"$HEREDOC_SQL"


  info "$SEED_VERSION 写入完成"
  printf '固定 seed 行使用稳定 ID；非 seed 数据未删除。\n'
}

main() {
  require_command mysql
  validate_configuration

  case "${1:-help}" in
    check)
      check_schema
      ;;
    seed)
      check_schema
      seed_data
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
