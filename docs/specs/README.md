# Spec 索引

这些 spec 是开发时的主依据；`DEVELOPMENT_PLAN.md` 负责排期，`ARCHITECTURE.md` 负责背景解释。

新增能力请从 [SPEC_TEMPLATE.md](./SPEC_TEMPLATE.md) 复制结构。

## 依赖顺序

| 顺序 | Spec | 对应里程碑 | 状态 |
| ---: | --- | --- | --- |
| 0 | [00-engineering-foundation.md](./00-engineering-foundation.md) | M0 | Draft |
| 1 | [01-domain-catalog-and-data.md](./01-domain-catalog-and-data.md) | M1 | Draft |
| 2 | [02-read-gateway.md](./02-read-gateway.md) | M2 | Draft |
| 3 | [03-runtime-graph.md](./03-runtime-graph.md) | M3 | Draft |
| 4 | [04-schema-rag-and-coverage.md](./04-schema-rag-and-coverage.md) | M4 | Draft |
| 5 | [05-memory-interrupts-and-artifacts.md](./05-memory-interrupts-and-artifacts.md) | M5 | Draft |
| 6 | [06-write-gateway-and-hitl.md](./06-write-gateway-and-hitl.md) | M6 | Deferred |
| 7 | [07-evaluation-and-release.md](./07-evaluation-and-release.md) | M7 | Draft |

## 当前计划中粒度偏粗的点

- M0 需要明确核心 Pydantic 模型、配置分层和 Trace ID 贯穿方式，否则后续模块会各自发明字典。
- M1 不能只说“7 张表和指标目录”，还要固定种子数据的边界案例、Golden Result 版本和权限样本。
- M2 必须先写 ReadGateway spec，因为它是所有 SQL 的唯一入口。
- M3 需要明确 Graph 状态机、不变量和预算终止条件，否则容易变成无限 ReAct。
- M4 需要定义 Coverage 和 SchemaGap 的数据契约，否则“补检”边界不清。
- M5 需要把 Working State、短期记忆、长期记忆和 Artifact 分开，否则会污染 Prompt 和权限。
- M6 建议默认延期，除非只读链路和评测已经稳定。
- M7 需要把评测用例格式、指标口径和消融实验固定下来。
