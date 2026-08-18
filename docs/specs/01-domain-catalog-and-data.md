# Spec 01：电商数据与语义目录

状态：`Implemented`

对应里程碑：M1

## 1. 范围

建立可重建、可评测的电商交易分析数据集和版本化语义目录，为 Gateway、RAG 和 Golden Case 提供确定性事实。

## 2. In Scope

- 8 张业务表：`shops`、`orders`、`order_items`、`products`、`categories`、`refunds`、`refund_items`、`users`。
- 版本化目录表：数据源、对象、字段、指标、业务预设、关系、别名和权限策略。
- 固定 seed 数据和 Golden Result。
- 至少两个用户的不同 `shop_id` 授权范围。
- 敏感字段分类。
- 应用操作者身份与业务买家身份分离；`users` 只表示业务买家，不表示登录用户。

## 3. Out of Scope

- 任意数据库适配。
- 自动从生产库同步 Schema。
- 大规模合成元数据索引，放到 M4。
- LLM 自动生成指标口径。

应用操作者、角色和 `shop_id` 授权范围来自 `PermissionContext`，不依赖业务 `users` 表中的某一行。

## 4. 业务表最小契约

| 表 | 必填字段 | 约束 |
| --- | --- | --- |
| `shops` | `shop_id`, `shop_name`, `region_code`, `region_name`, `status` | `shop_id` 主键；`status` 为 `ACTIVE` 或 `INACTIVE` |
| `users` | `user_id`, `phone`, `id_number`, `created_at` | `user_id` 主键；这里的 `user_id` 是业务买家，不是应用操作者；`phone` 和 `id_number` 标记为敏感字段 |
| `categories` | `category_id`, `parent_id`, `category_name` | `category_id` 主键；允许一级和二级品类 |
| `products` | `product_id`, `shop_id`, `category_id`, `product_name`, `status` | `product_id` 主键；`shop_id`、`category_id` 外键 |
| `orders` | `order_id`, `user_id`, `shop_id`, `status`, `paid_at`, `pay_amount`, `created_at` | `order_id` 主键；`user_id` 外键指向业务买家；`status` 至少包含 `PAID`、`CANCELLED`、`UNPAID`、`PAYMENT_FAILED` |
| `order_items` | `item_id`, `order_id`, `shop_id`, `product_id`, `quantity`, `item_paid_amount` | `item_id` 主键；订单、店铺和商品外键；金额使用定点数 |
| `refunds` | `refund_id`, `order_id`, `shop_id`, `status`, `refund_amount`, `refunded_at` | `status` 至少包含 `SUCCESS`、`PENDING`、`FAILED` |
| `refund_items` | `refund_item_id`, `refund_id`, `shop_id`, `order_item_id`, `refund_amount` | 退款、店铺和订单行外键；允许一笔订单多次退款 |

`orders.shop_id` 是事实表权限过滤的主路径。`order_items`、`products`、`categories`、`refunds` 和 `refund_items` 必须能沿已定义外键或 Verified Join 回溯到 `shops.shop_id`。无法回溯时，ReadGateway 必须拒绝查询。

## 5. 必备边界数据

seed 数据必须覆盖：

- 一笔订单多个商品；
- 部分退款和多次退款；
- 支付失败、取消、未支付订单；
- 没有数据的日期和地区；
- 两个用户拥有不同 `shop_id` 范围；
- 手机号、身份证等敏感字段；
- 商品名近义、地区名别名和状态枚举；
- 至少一组重复值，用于字段重复值追问。

## 6. 指标目录契约

每个指标必须包含：

```json
{
  "metric_id": "gmv",
  "name": "支付 GMV",
  "formula": "SUM(order_items.item_paid_amount)",
  "time_field": "orders.paid_at",
  "grain": ["day", "category", "shop"],
  "required_filters": ["orders.status = PAID"],
  "forbidden_join_patterns": ["raw_fact_to_raw_fact_sum"],
  "null_policy": "empty_denominator_returns_null",
  "rounding": "DECIMAL(2)",
  "version": "metric_v1"
}
```

首批指标和固定口径：

| 指标 | 定义 |
| --- | --- |
| GMV | `SUM(order_items.item_paid_amount)`，只统计 `orders.status = PAID` |
| 支付订单数 | `COUNT(DISTINCT orders.order_id)`，只统计已支付订单 |
| 支付买家数 | `COUNT(DISTINCT orders.user_id)`，只统计已支付订单 |
| 客单价 | GMV / 支付订单数；分母为 0 返回 `null` |
| 退款金额 | `SUM(refunds.refund_amount)`，只统计 `refunds.status = SUCCESS` |
| 金额退款率 | 退款金额 / GMV；分母为 0 返回 `null` |
| 品类 GMV | 通过 `order_items -> products -> categories` 聚合，不能直接把订单金额 Join 到商品行 |

时间口径默认使用指标的 `time_field`，首批指标中 GMV 和支付指标使用 `orders.paid_at`，退款金额和退款率使用 `refunds.refunded_at`。时间范围统一为 `[start, end)`，默认时区为 `Asia/Shanghai`，金额结果保留两位小数。

## 7. Golden Case 契约

每条 Golden Case 至少包含：

```json
{
  "case_id": "golden_001",
  "user_id": "u_east_user",
  "question": "昨天各品类 GMV 是多少？",
  "expected_task_type": "DATA_QUERY",
  "required_objects": ["orders", "order_items", "products", "categories"],
  "golden_sql": "SELECT c.category_name, ROUND(SUM(oi.item_paid_amount), 2) AS gmv FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id JOIN categories c ON c.category_id = p.category_id WHERE o.status = 'PAID' AND o.paid_at >= :start AND o.paid_at < :end AND o.shop_id IN (:allowed_shop_ids) GROUP BY c.category_id, c.category_name ORDER BY gmv DESC, c.category_name ASC;",
  "golden_result_ref": "golden_001.json",
  "expected_notes": ["paid orders only", "avoid duplicated fact join"],
  "data_version": "seed_v1",
  "time_anchor": "2026-08-16T10:00:00+08:00",
  "result_compare": {"row_order": "explicit", "numeric_scale": 2}
}
```

Golden SQL 中不能出现 `...`。每条 case 必须固定用户、时区、时间锚点、排序、数值精度和结果快照。

## 8. 不变量

- 数据库重建后 Golden Result 必须不变。
- 指标口径只能来自目录，不从 Prompt 临时编造。
- 事实表一对多 Join 后不能直接 `COUNT(*)` 或重复累计金额。
- Agent 可见字段必须先经过权限和敏感字段过滤。
- 应用操作者 `user_id` 不能被当成业务表中的 `orders.user_id` 使用。
- `shop_id` 权限过滤必须覆盖事实表和通过 Join 可达的维度表。

## 9. 验收标准

- 20 条 Golden Case 人工 SQL 全部可执行且结果稳定。
- 每个业务表、字段、指标和 Join 都有稳定 ID 与版本。
- 至少 2 个用户执行同一 Golden SQL 时能体现不同授权范围。
- 敏感字段能被目录分类识别。
- 8 张业务表的外键、Join 基数、时间字段和权限路径均有目录记录。

## 10. 测试证据

- migration 重建测试。
- seed 幂等测试。
- Golden SQL 结果快照测试。
- 指标目录完整性测试。
- 权限样本查询测试。

## 11. 数据库拆分与账号模型

业务数据与项目自有控制平面分离到两个物理数据库。这是从 spec 00 的
"reader 不可见应用控制表"出发的强化——把权限边界从应用层防御升格为
物理隔离。

### 11.1 数据库分工

| 数据库 | 表数量 | 内容 |
|---|---|---|
| `data_agent_ecommerce` | **8 张** | `shops`、`users`(业务买家)、`categories`、`products`、`orders`、`order_items`、`refunds`、`refund_items` —— 完全对应 §4 的"业务表最小契约",仅由 Reader 账号访问 |
| `data_agent_system` | **28 张** | 目录族 (16 张) + 认证 (3 张) + 运行时 (5 张) + 会话/记忆 (4 张) —— 由 Control 账号访问,Reader 物理上无连接凭据 |

### 11.2 账号模型 (4 个)

| 账号 | `data_agent_ecommerce` | `data_agent_system` | 用途 |
|---|---|---|---|
| `agent_migration` | ALL | ALL | 初始 DDL/migration、reseed、生产期间不用于任何在线查询 |
| `agent_control` | ❌ 禁止访问 | CRUD (全部) | API 进程持有;读写控制平面 |
| `agent_reader` | SELECT (仅 8 张业务表) | ❌ 禁止访问 | ReadGateway 持有;只读业务库 |
| `agent_writer` | SELECT + UPDATE(products.product_name) | ❌ 禁止访问 | WriteGateway 持有 |

Reader 账号的 MySQL URL 中 `database=data_agent_ecommerce`,账号 grant
里不出现 `data_agent_system.*`,物理上无法连接系统库。Control 反之亦然。
这一约束由 `mysql_env.sh harden` 强制,任何重新授权都必须从 setup 脚本走。

### 11.3 跨库外键

`app_user_shop_scopes.shop_id` 在 spec 01 §4 业务库中指向 `shops.shop_id`。
MySQL InnoDB 跨库 FK 行为不可靠,因此迁移 `001_system_identity_and_runtime.sql`
**删除了该 FK**,`shop_id` 保留为 `VARCHAR(64)` denormalized 字符串。

校验改为应用层 (请求时校验):
- `permission.py` 只查询 `data_agent_system.app_user_shop_scopes`,不连接业务库
- 运行时通过 `ReadGateway` 的 `WHERE shop_id IN (:allowed_shop_ids)` 自然过滤
- 启动期不连业务库校验 shop_id 存在性

### 11.4 配置文件

`config.yaml` 是运行时配置的唯一来源 (single source of truth)。`mysql:` 必有两个字段:
```yaml
mysql:
  business_database: data_agent_ecommerce   # Reader 账号的目标
  system_database: data_agent_system       # Control 账号的目标
```

环境变量只覆盖个别字段 (`backend/app/config.py` 列出可被覆盖的 key 列表);
不再保留 `config template.yaml` / `.secrets.yaml` / `.env.example` 这种并列文件。
生产环境用 `DRA_MYSQL_BUSINESS_DATABASE` / `DRA_MYSQL_SYSTEM_DATABASE` 等
env var 注入敏感值,本地开发直接填进 `config.yaml` (已被 `.gitignore` 排除)。

**任何对数据库配置的改动必须同时更新**:`config.yaml`、`backend/app/config.py`、
`scripts/mysql_env.sh`、`scripts/setup_databases.sh`。
验证脚本 `tests/test_database_split_config.py` 中的 `test_config_yaml_is_the_single_source_of_truth`
会在 CI/本地扫描:不存在 `config template.yaml` / `.secrets.yaml` / `.env.example` 这三个被取代的文件。

### 11.5 文件清单

任何对数据库结构的改动都涉及以下文件:

| 路径 | 作用 |
|---|---|
| `scripts/business_schema.sql` | 业务库 8 张表的 DDL |
| `migrations/001_system_identity_and_runtime.sql` ~ `008_system_registration.sql` | 系统库 28 张表的迁移,每个文件第一行必须是 `USE data_agent_system;` |
| `scripts/catalog_seed.sql` / `scripts/runtime_seed.sql` | 系统库种子,首行 `USE data_agent_system;` |
| `scripts/mock_mysql_data.sh` | 业务库 seed_v1,默认 `MYSQL_DATABASE=data_agent_ecommerce` |
| `scripts/mysql_env.sh` | 账号管理 (init/harden/check/grants/tables) |
| `scripts/setup_databases.sh` | 一键重建 (root) |
| `scripts/setup_local.sh` | 本地开发包装 (硬编码密码,仅限本机) |
| `config.yaml` | 运行时配置唯一来源 (含两个数据库字段) |
| `backend/app/repositories/data.py` | `MySQLDataRepository`,读 `business_database` |
| `backend/app/repositories/runtime.py` | `RuntimePersistence`/`mysql_url`,默认 `system_database` |
| `scripts/index_catalog.py` | 业务库 engine 跑 `MySQLSchemaCollector`,系统库 engine 跑 `MySQLCatalogRepository` |
| `tests/test_database_split_config.py` | MVP 验收测试,保证拆分不回归 |

### 11.6 验收检查

`tests/test_database_split_config.py` 包含 27 个静态/单元测试:
- 业务库 DDL 只创建 8 张业务表、不引用系统表
- 8 个迁移全部 `USE data_agent_system;`,不创建业务表
- 跨库 FK 已删除
- 种子文件 USE 正确
- `config.yaml` / `config template.yaml` 同时包含两个 database 字段,无遗留 `database: data_agent`
- `MySQLDataRepository` engine 绑定 `business_database`,`mysql_url()` 默认 `system_database`
- `scripts/mysql_env.sh` 同时管理两库,reader 永远不会被授予系统库权限

任何新增/重命名的迁移文件若未通过 §11.5 的清单检查,该 PR 即不可合入。
