# Task 3: MySQL 连接池（复用已有迁移）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T1 T2 · 交给：T4 T7 T11 · 里程碑：M1

## Boundary

| | |
| --- | --- |
| **Owns** | 三套 SQLAlchemy engine（admin / reader / writer），角色隔离可测。 |
| **In** | `backend/app/mysql/pool.py`、可选 `migrate.py`（检测表已存在则 no-op）、`tests/test_mysql_pool.py`。 |
| **Out** | `execute_read`、`execute_write`、Catalog sync、新业务表、新 SQL 迁移文件、查询 Skill。 |
| **Must not** | 再创建 `migrations/001_write_receipt_and_audit.sql`（回执/审计已在 `001_ecommerce_slice.sql`）；用 SQLite 冒充 MySQL；给 reader 写权限或给 writer `DROP`；在本 Task 执行业务查询或写入。 |

**Files:**
- Create: `backend/app/mysql/pool.py`
- Reuse: `migrations/mysql/001_ecommerce_slice.sql`、`002_ecommerce_seed.sql`、`003_tighten_writer_grants.sql`、`migrations/README.md`
- Create: `backend/app/mysql/migrate.py`（可选：检测表是否已存在，已存在则 no-op）
- Create: `tests/test_mysql_pool.py`

**Interfaces:**
- Consumes: `Settings.mysql.{admin,reader,writer}`
- Produces:
  - `get_engine(role: Literal["admin","reader","writer"])`

- [ ] **Step 1:** 无 MySQL 时测试 skip；有配置时 `reader` 执行 `INSERT` 必须失败，`writer` 不得执行 `DROP`，且 `writer` 不得 `UPDATE fact_order`。

```python
import pytest
from sqlalchemy import text
from backend.app.mysql.pool import get_engine

@pytest.mark.integration
def test_reader_cannot_write():
    eng = get_engine("reader")
    with eng.connect() as c, pytest.raises(Exception):
        c.execute(text("INSERT INTO da_write_receipt (operation_id) VALUES ('x')"))
```

- [ ] **Step 2–4:** 三套 engine。本地无 MySQL 时集成测试 skip。

- [ ] **Step 5: Commit** `feat: add split MySQL engines against existing ecommerce slice`
