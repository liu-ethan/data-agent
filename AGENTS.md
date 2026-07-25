# AGENTS.md

data-analysis-agent — 电商经营分析 Agent。按 `docs/06-开发计划.md` 分阶段开发，主链路优先。

## 需求与文档

- 需求入口：`docs/需求文档.md`，按功能拆分为 `docs/01`–`06`
- 不确定实现细节时：先查对应 docs，再看已有代码；仍不清楚则问用户，不要猜测
- 模糊、冲突或缺失的需求：先问用户，不要自行补全产品决策
- **实现计划 / 任务拆解**：（非复杂任务不需要，避免过度规划）用superpowers（或同类）产出的开发计划、implementation plan、任务清单，一律写在 `spec/`（勿放 `docs/`、仓库根目录或临时路径）；产品规格与架构仍以 `docs/` 为准，`spec/` 只承载「如何落地」的执行计划

## AI 开发规范

- **Spec 先行**：改行为前先对齐 `docs/`；落地步骤以 `spec/` 中的计划为准；文档未覆盖的决策先问用户，再改代码；实现后若行为变化，同步更新对应 docs
- **TDD 优先**：核心逻辑（Guardrail、权限、沙箱、鉴权、记忆槽位合并）先写失败测试，再写实现；修 bug 先补复现测试
- **解耦**：编排（LangGraph 节点）/ Tool / 确定性安全模块 / 可观测（AuditLog）分层；节点不直连 DB 写权限逻辑，经 Guardrail + Tool Registry
- **能确定就不调模型**：路由、权限、SQL 校验用代码；模型只负责理解、生成与解释
- **小步提交**：按 Phase 交付；一次只改相关文件，禁止顺手大重构

## Python / 配置（强制）

- Python：仅用 conda `/home/user/miniconda3/envs/python3.12`（或其 `bin/python` / `bin/pip`）；禁止系统 Python 与仓库 `.venv`
- 配置：仅用根目录 `config.yaml`（模板 `config_template.yaml`）；禁止 `.env`；勿提交密钥；测试可用 `APP_CONFIG`

## 代码规范

- 后端：Python 3.12 + FastAPI；前端：React + Vite + TypeScript + Tailwind
- 匹配现有风格；少改无关文件；不做未要求的抽象或过度设计
- Agent 各节点独立文件；SQL 权限校验与沙箱执行必须独立实现
- 不引入未约定依赖

## 前端

- 做前端 UI / 视觉设计时，必须先读并遵循 `frontend-design` skill
- `/` 为营销感登录注册页，`/app` 为工作台；布局与交互以 `docs/04-接口与前端.md` 为准
- 视觉细节模糊时先问用户
