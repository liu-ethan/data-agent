# Design: LLM Prompt 外置（prompts/*.yaml）与企业级文案

日期：2026-07-26  
状态：已实现（实现计划见 `spec/2026-07-26-prompts-yaml-plan.md`）  
范围：将 7 处 LLM system/user 文案从 Python 硬编码抽出到 `backend/app/prompts/*.yaml`，按企业级结构重写；提供 `render()` 加载层。不改 Agent 图拓扑、Guardrail、沙箱、Tool 契约（tool description 暂不外置）。

## 1. 背景与目标

现状：Intent / ReAct / SQLGenerator / SQLRepairer / ChartPlanner / AnswerComposer / 会话标题 的 prompt 均为 py 内短句硬编码，难评审、难迭代，且质量偏「三两句交代」。

目标（已与用户确认）：

1. 目录采用 **C1**：`backend/app/prompts/` 下一节点一 yaml。
2. 文案按企业级结构重写（角色 / 任务 / 约束 / 输出契约 / 边界），语言统一中文。
3. 节点只通过加载层取文案，禁止在业务 py 里再堆长 prompt。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 布局 | C1：`backend/app/prompts/<node>.yaml` |
| 覆盖范围 | 仅 7 个 LLM 的 system/user；**不含** tool description、澄清话术、写成功提示 |
| 模板引擎 | 标准库 `str.format_map`；字面量 `{{` / `}}`；不引入 Jinja |
| 配置耦合 | 默认目录固定为包内 `prompts/`；可选 `APP_PROMPTS_DIR` 供测试覆盖 |
| Intent Schema | 仍禁止在 Intent prompt 灌全库表结构（对齐 `docs/03`） |
| 语言 | 中文（面向电商经营分析产品） |

## 3. 架构总览

```text
backend/app/prompts/
  intent_analyzer.yaml
  react_agent.yaml
  sql_generator.yaml
  sql_repairer.yaml
  chart_planner.yaml
  answer_composer.yaml
  session_title.yaml
  __init__.py          # render / load / clear_cache

节点 / 模块:
  intent_analyzer.py ──┐
  react_agent.py       │
  sql_generator.py     ├──► prompts.render(name, **vars)
  sql_repairer.py      │         → {"system", "user"}
  chart_planner.py     │
  answer_composer.py   │
  memory/title.py    ──┘
```

行为不变：仍走 `chat_completion` / `chat_completion_with_tools`；仅消息文本来源变更。

## 4. YAML 契约

每个文件：

```yaml
version: "1"
description: "给人看的说明，不送模型"

system: |
  ... 含 {placeholders} 与字面量 {{ }} ...

user: |
  ... 含 {placeholders} ...
```

要求：

- 必须有非空 `system`、`user`
- `version` / `description` 可选但模板里保留
- 占位符名与调用方 kwargs 一致；缺失 → `KeyError` / 明确 `PromptRenderError`
- 多余 kwargs 忽略或报错：选择 **报错**（防拼写漂移漏改）——实现用自定义 `SafeDict` 或校验所需 key 集合

推荐每个 `system` 固定小节：

1. 角色  
2. 任务目标  
3. 输入说明  
4. 硬约束（禁止项）  
5. 业务/决策规则  
6. 输出契约  
7. 边界与降级  

## 5. 七文件占位符

| 文件 | system 占位符 | user 占位符 |
|------|---------------|-------------|
| `intent_analyzer` | `intent_list`, `metrics`, `dimensions`, `time_ranges` | `question`, `context_block`（可为空串） |
| `react_agent` | `slots_json` | `question` |
| `sql_generator` | `schema_json`, `metric_specs_json`, `slots_json` | `question` |
| `sql_repairer` | （无或仅静态） | `question`, `sql`, `error`, `schema_json` |
| `chart_planner` | （静态） | `payload_json` |
| `answer_composer` | （静态） | `question`, `result_json` |
| `session_title` | `max_chars` | `question`, `summary` |

调用方负责 JSON pretty / 脱敏（title 仍 `strip_sensitive`）；yaml 只负责文案骨架。

## 6. 加载层 API

```python
# backend/app/prompts/__init__.py

class PromptRenderError(ValueError): ...

def render(name: str, **variables: Any) -> dict[str, str]:
    """Load prompts/<name>.yaml, format system/user, return {"system","user"}."""

def clear_cache() -> None: ...
```

- 首次按 name 读盘并缓存 parsed yaml；`clear_cache` 供测试
- `APP_PROMPTS_DIR` 若设置则覆盖默认目录
- 不把整份 yaml 打进 AuditLog 以外的额外通道；现有 `prompt_input` 日志仍记录渲染后的 messages

## 7. 节点改动要点

| 模块 | 改动 |
|------|------|
| `nodes/intent_analyzer.py` | `build_intent_prompt` 调 `render`；上下文拼成 `context_block` |
| `nodes/react_agent.py` | `_initial_messages` 用 render |
| `sql_generator.py` | `generate_sql` 用 render |
| `nodes/sql_repairer.py` | 用 render |
| `chart_planner.py` | `_build_messages` 用 render |
| `answer_composer.py` | `compose_answer` 用 render；写操作短句仍代码内 |
| `memory/title.py` | 用 render；`max_chars` 来自 Settings |

## 8. 测试（TDD）

1. **loader 单测**：缺文件 / 缺 key / 缺占位符 → 失败；正常 render 含注入值。  
2. **节点烟测**：现有 mock `chat_completion` 的测试仍过；必要时断言传入 messages 的 system 含关键契约关键字（如「只输出 JSON」「propose_sql」）。  
3. 不强制对「文案文学性」做金样全文比对（避免脆）。

## 9. 文档同步

- `docs/03-Agent设计.md`：Intent Prompt 约束处补充「文案外置路径 `backend/app/prompts/`」一句。  
- `docs/01` 目录结构若列出 prompts 则补一行。  
- 不把完整企业级长文粘进 docs（以 yaml 为准）。

## 10. 非目标

- 不外置 tool description / OpenAI tools schema  
- 不外置 ClarificationChecker 固定话术  
- 不改 route / 权限 / SQL 校验逻辑  
- 不引入新第三方依赖  

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `{` 与 JSON 示例冲突 | yaml 内字面量用 `{{` `}}`；单测覆盖 |
| 文案过长抬 token | 结构完整但克制示例；Intent 仍禁 Schema |
| 缓存导致改 yaml 不生效 | 进程重启生效；测试 `clear_cache` |

## 12. 验收标准

- [x] 7 个 yaml 就位且无业务 py 内长 system 字符串  
- [x] `render` + 节点接线完成  
- [x] 相关单测通过  
- [x] docs 路径说明已同步  
