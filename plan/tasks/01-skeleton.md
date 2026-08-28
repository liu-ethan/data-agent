# Task 1: 工程骨架与配置加载

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：无 · 交给：所有后续 Task · 里程碑：M0

## Boundary

| | |
| --- | --- |
| **Owns** | 项目可安装、`Settings` 能从 YAML 加载、空 FastAPI 能启动、pytest 能跑。 |
| **In** | `pyproject.toml`、`backend/app/config.py`、`logging.py`、最小 `main.py`、`tests/test_config.py`、安装用 `README.md`。补 `.gitignore` 缺口。 |
| **Out** | `types.py`、MySQL 连接、Catalog、任何 Skill/Coordinator、前端、评测、新 migration、新 config 键。 |
| **Must not** | 覆盖已有 `config.example.yaml` / `.gitignore` 另写一份；把真实密钥写进 README 或测试；创建根目录 `migrations/001_*.sql`；提前实现后续模块的空壳「方便以后」。 |

**Files:**
- Create: `pyproject.toml`
- Reuse: `config.example.yaml`、`.gitignore`（已存在，不要另写一份）
- Create: `backend/app/config.py`
- Create: `backend/app/logging.py`
- Create: `backend/app/main.py`
- Create: `tests/test_config.py`
- Create: `README.md`（安装与启动，不含密钥）

**Interfaces:**
- Consumes: `plan/config-needed.md` 的字段名（必须与已有 `config.yaml` / `config.example.yaml` 一致）
- Produces: `load_settings(path: str | None = None) -> Settings`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from pathlib import Path
from backend.app.config import load_settings

def test_load_example_config(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(Path("config.example.yaml").read_text(), encoding="utf-8")
    s = load_settings(str(p))
    assert s.mysql.port == 3306
    assert s.mysql.reader.user
    assert s.write.max_affected_rows == 100
    assert s.schema_rag.max_gap_rounds == 2
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError` 或 `config.example.yaml` 不存在。

- [ ] **Step 3: 最小实现**

`pyproject.toml` 依赖：`fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `pyyaml`, `langgraph`, `langchain-openai`, `sqlglot`, `sqlalchemy`, `pymysql`, `duckdb`, `pyarrow`, `rank-bm25`, `httpx`, `pytest`, `ruff`。

`Settings` 用嵌套 Pydantic model，字段与根目录已有 `config.yaml` / `config.example.yaml` 一一对应。`load_settings()` 读取路径优先级：参数 > 环境变量 `DATA_AGENT_CONFIG` > `./config.yaml`。

`.gitignore` 已包含：`config.yaml`、`data/`、`.env`、`frontend/node_modules/`、`__pycache__/`、`*.parquet`。缺什么补什么，不要覆盖已有规则。

- [ ] **Step 4: 测试通过**

```bash
pytest tests/test_config.py -v
```

Expected: PASS
