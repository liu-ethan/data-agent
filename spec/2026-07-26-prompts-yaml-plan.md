# LLM Prompt 外置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 7 处 LLM system/user 文案外置到 `backend/app/prompts/*.yaml`，按企业级结构重写，经 `prompts.render()` 注入节点。

**Architecture:** 包内一节点一 yaml；`app.prompts.render(name, **vars)` 读盘缓存、`str.format_map` 严格校验占位符；节点只拼 messages，不再内嵌长文案。

**Tech Stack:** Python 3.12、PyYAML（已有）、pytest；无新依赖。Python：`/home/user/miniconda3/envs/python3.12/bin/python`。

## Global Constraints

- 只在本仓库 `main` 改代码；禁止 git worktree
- Agent **不** `git commit` / `git push`（步骤中的 commit 改为「汇报建议 message，等用户提交」）
- 配置仍用根目录 `config.yaml`；prompt 目录默认包内，测试可用 `APP_PROMPTS_DIR`
- 不引入 Jinja；不外置 tool description / 澄清话术
- Intent 禁止灌全库 Schema（`docs/03`）
- TDD：先失败测试再实现

---

## File map

| 路径 | 职责 |
|------|------|
| `backend/app/prompts/__init__.py` | `PromptRenderError` / `render` / `clear_cache` / 目录解析 |
| `backend/app/prompts/*.yaml` × 7 | 企业级 system/user 文案 |
| `backend/tests/test_prompts.py` | loader 单测 |
| `backend/app/agent/nodes/intent_analyzer.py` 等 7 调用点 | 改用 `render` |
| `docs/03-Agent设计.md`、`docs/01-需求总览.md` | 路径说明 |

---

### Task 1: Prompt loader（TDD）

**Files:**
- Create: `backend/tests/test_prompts.py`
- Create: `backend/app/prompts/__init__.py`
- Test: `backend/tests/test_prompts.py`

**Interfaces:**
- Produces: `PromptRenderError`, `render(name: str, **variables) -> dict[str, str]`, `clear_cache() -> None`
- Env: `APP_PROMPTS_DIR` 可选覆盖目录

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_prompts.py
import textwrap
from pathlib import Path

import pytest
import yaml


@pytest.fixture()
def prompts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PROMPTS_DIR", str(tmp_path))
    from app import prompts

    prompts.clear_cache()
    yield tmp_path
    prompts.clear_cache()


def _write(dir_path: Path, name: str, system: str, user: str):
    (dir_path / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {"version": "1", "description": "t", "system": system, "user": user},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_render_injects_variables(prompts_dir):
    _write(
        prompts_dir,
        "demo",
        "Hello {name}. Use {{literal}} braces.",
        "Q: {question}",
    )
    from app.prompts import render

    out = render("demo", name="Ada", question="GMV?")
    assert out["system"] == "Hello Ada. Use {literal} braces."
    assert out["user"] == "Q: GMV?"


def test_render_missing_file(prompts_dir):
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="not found"):
        render("nope")


def test_render_missing_placeholder(prompts_dir):
    _write(prompts_dir, "demo", "Hi {name}", "Q")
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="Missing"):
        render("demo")


def test_render_extra_kwargs(prompts_dir):
    _write(prompts_dir, "demo", "Hi {name}", "Q")
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="Unexpected"):
        render("demo", name="Ada", unused=1)


def test_render_requires_system_and_user(prompts_dir):
    (prompts_dir / "bad.yaml").write_text(
        yaml.safe_dump({"system": "only"}, allow_unicode=True),
        encoding="utf-8",
    )
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="user"):
        render("bad")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend && \
/home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_prompts.py -v
```

Expected: FAIL（`app.prompts` 不存在或 API 不全）

- [ ] **Step 3: 实现 loader**

```python
# backend/app/prompts/__init__.py
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any

import yaml

_DEFAULT_DIR = Path(__file__).resolve().parent


class PromptRenderError(ValueError):
    pass


def _prompts_dir() -> Path:
    override = os.environ.get("APP_PROMPTS_DIR")
    if override:
        return Path(override)
    return _DEFAULT_DIR


def clear_cache() -> None:
    _load_raw.cache_clear()


def _field_names(template: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if not field_name:
            continue
        names.add(field_name.split(".")[0].split("[")[0])
    return names


@lru_cache(maxsize=32)
def _load_raw(name: str, dir_key: str) -> dict[str, str]:
    path = Path(dir_key) / f"{name}.yaml"
    if not path.is_file():
        raise PromptRenderError(f"Prompt file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise PromptRenderError(f"Prompt root must be a mapping: {path}")
    system = data.get("system")
    user = data.get("user")
    if not isinstance(system, str) or not system.strip():
        raise PromptRenderError(f"Prompt missing non-empty system: {path}")
    if not isinstance(user, str) or not user.strip():
        raise PromptRenderError(f"Prompt missing non-empty user: {path}")
    return {"system": system, "user": user}


def render(name: str, **variables: Any) -> dict[str, str]:
    raw = _load_raw(name, str(_prompts_dir()))
    required = _field_names(raw["system"]) | _field_names(raw["user"])
    provided = set(variables)
    missing = required - provided
    if missing:
        raise PromptRenderError(
            f"Missing prompt variables for {name}: {sorted(missing)}"
        )
    unexpected = provided - required
    if unexpected:
        raise PromptRenderError(
            f"Unexpected prompt variables for {name}: {sorted(unexpected)}"
        )
    try:
        return {
            "system": raw["system"].format_map(variables),
            "user": raw["user"].format_map(variables),
        }
    except (KeyError, ValueError) as exc:
        raise PromptRenderError(f"Failed to render prompt {name}: {exc}") from exc
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_prompts.py -v
```

Expected: PASS

- [ ] **Step 5: 汇报（不 commit）**

建议 message：`feat(prompts): add yaml prompt loader with strict format vars`

---

### Task 2: 写入 7 个企业级 yaml

**Files:**
- Create: `backend/app/prompts/intent_analyzer.yaml`
- Create: `backend/app/prompts/react_agent.yaml`
- Create: `backend/app/prompts/sql_generator.yaml`
- Create: `backend/app/prompts/sql_repairer.yaml`
- Create: `backend/app/prompts/chart_planner.yaml`
- Create: `backend/app/prompts/answer_composer.yaml`
- Create: `backend/app/prompts/session_title.yaml`

**Interfaces:**
- Consumes: Task 1 `render`
- Produces: 磁盘上 7 个可 `render` 的模板（占位符见 design §5）

每个文件必须含：`version` / `description` / `system` / `user`；`system` 含七段标题（角色、任务目标、输入说明、硬约束、业务规则、输出契约、边界与降级）；中文；字面量花括号用 `{{` `}}`。

占位符（必须与接线一致）：

| name | vars |
|------|------|
| intent_analyzer | system: `intent_list,metrics,dimensions,time_ranges`；user: `question,context_block` |
| react_agent | system: `slots_json`；user: `question` |
| sql_generator | system: `schema_json,metric_specs_json,slots_json`；user: `question` |
| sql_repairer | system: 无占位符；user: `question,sql,error,schema_json` |
| chart_planner | system: 无；user: `payload_json` |
| answer_composer | system: 无；user: `question,result_json` |
| session_title | system: `max_chars`；user: `question,summary` |

关键契约关键字（供 Task 4 烟测）：

- intent：`只输出 JSON`、`禁止` + Schema / 表结构
- react：`propose_sql`、`禁止执行`
- sql_generator / sql_repairer：`只输出` + `SQL`、`禁止 DDL`
- chart：`type`、`line|bar|pie|table`
- answer：`不得编造`、`中文`
- title：`不超过{max_chars}` 或渲染后含数字

- [ ] **Step 1: 写占位符契约测试（用正式文件名，暂写最小 yaml 也可先跳到 Step 2 直接写全文）**

在 `test_prompts.py` 追加（针对默认目录正式文件，不设 `APP_PROMPTS_DIR`）：

```python
def test_bundled_prompts_render_with_required_vars():
    from app.prompts import clear_cache, render

    clear_cache()
    intent = render(
        "intent_analyzer",
        intent_list="x",
        metrics="gmv",
        dimensions="channel",
        time_ranges="last_month",
        question="q",
        context_block="",
    )
    assert "只输出 JSON" in intent["system"] or "JSON" in intent["system"]
    assert "q" in intent["user"]

    react = render("react_agent", slots_json="{}", question="q")
    assert "propose_sql" in react["system"]

    sql = render(
        "sql_generator",
        schema_json="[]",
        metric_specs_json="[]",
        slots_json="{}",
        question="q",
    )
    assert "SQL" in sql["system"]

    repair = render(
        "sql_repairer",
        question="q",
        sql="select 1",
        error="e",
        schema_json="{}",
    )
    assert "SQL" in repair["system"]

    chart = render("chart_planner", payload_json="{}")
    assert "type" in chart["system"]

    ans = render("answer_composer", question="q", result_json="{}")
    assert "中文" in ans["system"] or "不得编造" in ans["system"]

    title = render("session_title", max_chars=10, question="q", summary="s")
    assert "10" in title["system"]
```

- [ ] **Step 2: 跑测确认失败（文件不存在）**

```bash
/home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_prompts.py::test_bundled_prompts_render_with_required_vars -v
```

- [ ] **Step 3: 写入 7 个完整企业级 yaml**

实现时按 design 七段结构写满中文正文（勿再缩成两三句）。示例骨架（`intent_analyzer.yaml`；其余同结构展开）：

```yaml
version: "1"
description: "IntentAnalyzer — 意图/槽位/分流，禁止灌 Schema"

system: |
  # 角色
  你是电商经营分析 Agent 的意图分析组件，服务于内部经营看板与问数场景。

  # 任务目标
  根据用户自然语言问题，产出结构化 JSON：意图、置信度、分析槽位、路由模式，以及是否需要澄清。

  # 输入说明
  - 封闭 intent 枚举：{intent_list}
  - 指标词表 metrics：{metrics}
  - 维度/group_by 词表：{dimensions}
  - 时间范围词表 time_range：{time_ranges}
  - 用户问题与可选会话上下文见 user 消息（上下文仅辅助理解追问，不得覆盖用户明确表达）

  # 硬约束
  - 禁止输出 Markdown 解释或多余文本；只输出一个 JSON 对象（可包在 ```json 代码块中）
  - 禁止在本阶段使用或臆造全库表结构、列名、样例行、底层 SQL
  - slots.metrics / group_by / time_range 只能使用给定词表中的值；无法映射时勿硬猜
  - 不得编造业务事实

  # 业务规则
  - GMV 默认指支付金额口径，不必仅因「GMV」二字发起澄清
  - route_mode=react：单指标、TopN、路径清晰
  - route_mode=coordinator：多指标对比、归因、多步协作
  - 「表现最好」等未指明指标，或「最近」等未指明时间且无可用默认 → need_clarification=true，并给出具体澄清问句

  # 输出契约
  输出 JSON 字段：
  - intent (string)
  - confidence (number 0-1)
  - summary (string，一句话复述分析目标)
  - route_mode ("react" | "coordinator")
  - slots: {{ "metrics": string[], "time_range": string|null, "group_by": string[], "top_n": number|null, "write_intent": bool, "filters": object|null }}
  - need_clarification (bool)
  - clarification_question (string|null)

  # 边界与降级
  - 无法归类时 intent 可用 unknown，route_mode 默认 react
  - 信息不足时优先澄清，不要假装已理解

user: |
  {question}
  {context_block}
```

其余 6 个文件按同规范写全（react 强调工具顺序与 `propose_sql`、禁执行；sql_* 强调只读/单条 SQL/禁 DDL；chart 强调列名存在性；answer 强调不编造数字；title 强调只输出标题且不超过 `{max_chars}`）。

- [ ] **Step 4: 跑 `test_prompts.py` 全绿**

- [ ] **Step 5: 汇报** 建议 message：`feat(prompts): add enterprise prompt yaml templates`

---

### Task 3: 节点接线（7 调用点）

**Files:**
- Modify: `backend/app/agent/nodes/intent_analyzer.py`（`build_intent_prompt`）
- Modify: `backend/app/agent/nodes/react_agent.py`（`_initial_messages`）
- Modify: `backend/app/agent/sql_generator.py`（`generate_sql`）
- Modify: `backend/app/agent/nodes/sql_repairer.py`（`sql_repairer`）
- Modify: `backend/app/agent/chart_planner.py`（`_build_messages`）
- Modify: `backend/app/agent/answer_composer.py`（`compose_answer`）
- Modify: `backend/app/agent/memory/title.py`（`generate_session_title`）

**Interfaces:**
- Consumes: `from app.prompts import render`
- Produces: messages 仍为 `[system, user]`，行为与 mock 测试兼容

接线模式（每处）：

```python
from app.prompts import render

parts = render("sql_generator", schema_json=..., metric_specs_json=..., slots_json=..., question=question)
messages = [
    {"role": "system", "content": parts["system"]},
    {"role": "user", "content": parts["user"]},
]
```

Intent 特殊：`context_block` 在 py 里拼好（无上下文时传 `""`；有上下文时用与现逻辑等价的前缀 + 行），再传入 render。  
Chart：`payload_json=json.dumps(payload, ensure_ascii=False)`。  
Answer：写操作短句保留代码内。  
Title：`max_chars=get_settings().memory_session_title_max_chars`。

- [ ] **Step 1: 跑现有相关测试基线**

```bash
/home/user/miniconda3/envs/python3.12/bin/python -m pytest \
  tests/test_session_title.py tests/test_phase5_pipeline.py \
  tests/test_graph_pipeline.py tests/test_chat_api.py -q --tb=line
```

- [ ] **Step 2: 改 7 个调用点，删除硬编码长 system 字符串**

- [ ] **Step 3: 再跑 Step 1 命令 + `tests/test_prompts.py`**

Expected: PASS

- [ ] **Step 4: 汇报** 建议 message：`refactor(agent): load LLM prompts from prompts/*.yaml`

---

### Task 4: 文档同步与总验收

**Files:**
- Modify: `docs/03-Agent设计.md`（Intent Prompt 约束小节，补一句外置路径）
- Modify: `docs/01-需求总览.md`（目录结构 `app/` 下增加 `prompts/`）
- Modify: `spec/2026-07-26-prompts-yaml-design.md`（状态改为已实现）

- [ ] **Step 1: 更新 docs**

在 `docs/03` Intent「Prompt 约束」后增加：

> 文案外置：`backend/app/prompts/intent_analyzer.yaml`（经 `app.prompts.render` 加载）；本阶段仍禁止灌全库 Schema。

在 `docs/01` 目录树 `app/` 下增加：

```text
│   │   ├── prompts/                 # LLM system/user 文案（*.yaml）
```

- [ ] **Step 2: 全量后端测试**

```bash
cd .../backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests -q --tb=line
```

Expected: 全绿

- [ ] **Step 3: 人工检查** `rg -n '你是|You are a' backend/app/agent --glob '*.py'` 不应再命中 LLM system 长文案（澄清话术等非 LLM 除外）

- [ ] **Step 4: 汇报验收** 对照 design §12；建议 message：`docs: note prompts yaml externalization`

---

## Spec coverage check

| Design 项 | Task |
|-----------|------|
| C1 七文件 | T2 |
| render / cache / APP_PROMPTS_DIR | T1 |
| 严格占位符 | T1 |
| 企业级中文文案 | T2 |
| 7 节点接线 | T3 |
| 测试 | T1–T3 |
| docs | T4 |
| 非目标（tool desc 等） | 不实现 |

## Placeholder scan

无 TBD / 「适当处理」类步骤。
