# Spec 索引

这些 spec 是开发时的主依据；`DEVELOPMENT_PLAN.md` 负责排期，`ARCHITECTURE.md` 负责背景解释。

新增能力请从 [SPEC_TEMPLATE.md](./SPEC_TEMPLATE.md) 复制结构。

## 依赖顺序

| 顺序 | Spec | 对应里程碑 | 状态 |
| ---: | --- | --- | --- |
| 0 | [00-engineering-foundation.md](./00-engineering-foundation.md) | M0 | Ready |
| 1 | [01-domain-catalog-and-data.md](./01-domain-catalog-and-data.md) | M1 | Ready |
| 2 | [02-read-gateway.md](./02-read-gateway.md) | M2 | Ready |
| 3 | [03-runtime-graph.md](./03-runtime-graph.md) | M3 | Ready |
| 4 | [04-schema-rag-and-coverage.md](./04-schema-rag-and-coverage.md) | M4 | Ready |
| 5 | [05-memory-interrupts-and-artifacts.md](./05-memory-interrupts-and-artifacts.md) | M5 | Ready |
| 6 | [06-write-gateway-and-hitl.md](./06-write-gateway-and-hitl.md) | M6 | Deferred |
| 7 | [07-evaluation-and-release.md](./07-evaluation-and-release.md) | M7 | Ready |
| 8 | [08-frontend-experience.md](./08-frontend-experience.md) | M0/M3/M5/M7 | Ready |

## 当前计划中粒度偏粗的点

- M0 已固定核心 Pydantic 模型、配置优先级、错误码和 Trace 版本字段。
- M1 已固定 8 张业务表、店铺权限路径、指标公式、时间锚点和结果比较规则。
- M2 已固定 QuerySpec、PermissionContext、RLS 注入和成本拒绝语义。
- M3 已固定包含 `EXECUTE` 的 Graph 状态机、API 请求和 SSE 事件契约。
- M4 已固定检索结果版本、权限前置过滤、分数范围和 Token 预算计算方法。
- M5 已固定 Checkpoint 乐观锁、Interrupt resume、Artifact 访问校验和幂等规则。
- M6 建议默认延期，除非只读链路和评测已经稳定。
- M7 已固定评测用例、结果误差、指标口径和可复现版本信息。
- Spec 08 已固定前端工作台、SSE、响应式交互、无障碍、Playwright 和 CORS 验收；前端不是后端的附属页面。
