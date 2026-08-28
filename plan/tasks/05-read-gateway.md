# Task 5: SQL 安全网关（只读策略）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 T4 · 交给：T7 T10 · 里程碑：M2

这是查询正确性的硬闸门，不依赖 LLM。

## Boundary

| | |
| --- | --- |
| **Owns** | 对 `CompiledQuery` 做只读安全判定，返回 `GatewayDecision`。 |
| **In** | `gateway/ast.py`、`read_policy.py`、`explain.py`、`tests/test_read_gateway.py`。 |
| **Out** | 执行 SQL、MetricCompiler、写入网关、Skill 图、HITL、改 SQL 文本。 |
| **Must not** | 让 LLM 判安全；接受非参数化 SQL；修 SQL（网关只判，不改）；把 `too_broad` 当成可自动修复；假设 MySQL 账号会挡敏感列（列级权限在本网关）。 |

**Files:**
- Create: `backend/app/gateway/ast.py`
- Create: `backend/app/gateway/read_policy.py`
- Create: `backend/app/gateway/explain.py`
- Create: `tests/test_read_gateway.py`

**Interfaces:**
- Consumes: `QueryTask`, `CatalogSnapshot`, `CompiledQuery`
- Produces: `GatewayDecision(ok: bool, reason: str | None, kind: Literal["unsafe","too_broad","ok"])`

`parse_mysql(sql: str) -> exp.Expression`：SQLGlot `read="mysql"`。多语句、非 SELECT、解析失败、SQL 含字符串字面量用户筛选 → `unsafe`。

只读规则（全部要有测试用例，对应评测危险/合法边界骨架）：

1. 恰好一条 `SELECT`。
2. 禁止 `SELECT *`。
3. 禁止 `FOR UPDATE` / `LOCK IN SHARE MODE` / `INTO OUTFILE` / `LOAD_FILE` / `SLEEP` / `BENCHMARK` / `UPDATEXML` 等。
4. 出现的表、字段 ⊆ 本次任务允许集（`QueryTask` + 权限 + 本次召回的审核关系）。
5. 每个 JOIN 必须能在本次召回的审核关系中找到 `(left,right,on)`。
6. 若指标 `grain_table` 为一侧，JOIN 对侧是 `one_to_many` 且聚合未按正确 grain 去重 → `unsafe`（重复统计）。
7. 无时间约束的明细（无聚合且无 `limit` 或 limit 过大）→ `too_broad`。
8. `EXPLAIN` 估计扫描行数 > `query.max_explain_rows` → `too_broad`。
9. `is_sensitive=1` 的列出现在 SELECT/WHERE/JOIN ON → `unsafe`。
10. 用户筛选只能以绑定参数出现；检测到把 `FilterCond.value` 内联进 SQL → `unsafe`。
11. SQLGlot 只解析，不解释业务含义。

- [ ] **Step 1: 写失败测试（节选）**

```python
from backend.app.gateway.read_policy import check_read_sql
from backend.app.types import CompiledQuery

def test_rejects_select_star(task, catalog):
    q = CompiledQuery(sql="SELECT * FROM fact_order", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"

def test_rejects_sensitive_column(task, catalog_with_sensitive_nick):
    q = CompiledQuery(sql="SELECT nick_name FROM dim_user WHERE id = :id", params={"id": 1})
    d = check_read_sql(q, task, catalog_with_sensitive_nick, allowed_joins=[])
    assert d.ok is False and d.kind == "unsafe"

def test_rejects_inlined_filter_value(task, catalog):
    q = CompiledQuery(sql="SELECT id FROM dim_sku WHERE sku_name = 'x' OR 1=1", params={})
    d = check_read_sql(q, task, catalog, allowed_joins=[])
    assert d.ok is False
```

- [ ] **Step 5: Commit** `feat: add SQLGlot read gateway with join and fanout rules`
