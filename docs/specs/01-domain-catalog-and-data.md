# Spec 01：电商数据与语义目录

状态：`Draft`

对应里程碑：M1

## 1. 范围

建立可重建、可评测的电商交易分析数据集和版本化语义目录，为 Gateway、RAG 和 Golden Case 提供确定性事实。

## 2. In Scope

- 7 张业务表：`orders`、`order_items`、`products`、`categories`、`refunds`、`refund_items`、`users`。
- 版本化目录表：数据源、对象、字段、指标、业务预设、关系、别名和权限策略。
- 固定 seed 数据和 Golden Result。
- 至少两个用户的不同 `shop_id` 授权范围。
- 敏感字段分类。

## 3. Out of Scope

- 任意数据库适配。
- 自动从生产库同步 Schema。
- 大规模合成元数据索引，放到 M4。
- LLM 自动生成指标口径。

## 4. 必备边界数据

seed 数据必须覆盖：

- 一笔订单多个商品；
- 部分退款和多次退款；
- 支付失败、取消、未支付订单；
- 没有数据的日期和地区；
- 两个用户拥有不同 `shop_id` 范围；
- 手机号、身份证等敏感字段；
- 商品名近义、地区名别名和状态枚举；
- 至少一组重复值，用于字段重复值追问。

## 5. 指标目录契约

每个指标必须包含：

```json
{
  "metric_id": "gmv",
  "name": "支付 GMV",
  "formula": "SUM(orders.pay_amount)",
  "time_field": "orders.paid_at",
  "grain": ["day", "category", "shop"],
  "required_filters": ["orders.status = PAID"],
  "forbidden_join_patterns": ["raw_fact_to_raw_fact_sum"],
  "version": "metric_v1"
}
```

首批指标：GMV、支付订单数、支付买家数、客单价、退款金额、金额退款率、品类 GMV。

## 6. Golden Case 契约

每条 Golden Case 至少包含：

```json
{
  "case_id": "golden_001",
  "user_id": "u_east_user",
  "question": "昨天各品类 GMV 是多少？",
  "expected_task_type": "DATA_QUERY",
  "required_objects": ["orders", "order_items", "products", "categories"],
  "golden_sql": "SELECT ...",
  "golden_result_ref": "golden_001.json",
  "expected_notes": ["paid orders only", "avoid duplicated fact join"]
}
```

## 7. 不变量

- 数据库重建后 Golden Result 必须不变。
- 指标口径只能来自目录，不从 Prompt 临时编造。
- 事实表一对多 Join 后不能直接 `COUNT(*)` 或重复累计金额。
- Agent 可见字段必须先经过权限和敏感字段过滤。

## 8. 验收标准

- 20 条 Golden Case 人工 SQL 全部可执行且结果稳定。
- 每个业务表、字段、指标和 Join 都有稳定 ID 与版本。
- 至少 2 个用户执行同一 Golden SQL 时能体现不同授权范围。
- 敏感字段能被目录分类识别。

## 9. 测试证据

- migration 重建测试。
- seed 幂等测试。
- Golden SQL 结果快照测试。
- 指标目录完整性测试。
- 权限样本查询测试。

