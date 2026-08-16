# Data Runtime Agent 文档入口

本文档目录把“目标架构”“开发计划”和“可执行规格”分开维护，避免开发时在一份大文档里同时找背景、边界和验收条件。

## 推荐阅读顺序

1. [ARCHITECTURE.md](./ARCHITECTURE.md)：完整目标架构与设计背景，回答“为什么这样设计”。
2. [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md)：6 周里程碑和优先级，回答“先做什么、后做什么”。
3. [SPEC_DRIVEN_DEVELOPMENT.md](./SPEC_DRIVEN_DEVELOPMENT.md)：基于 spec 开发的规则，回答“每个任务怎么算可开工、可验收”。
4. [specs/README.md](./specs/README.md)：规格索引和依赖顺序。
5. [data-runtime-agent-interview-demo.html](./data-runtime-agent-interview-demo.html)：面试演示页。
6. [../scripts/README.md](../scripts/README.md)：本地 MySQL 初始化和排查脚本。

## 文档职责

| 文档 | 职责 | 不放什么 |
| --- | --- | --- |
| `ARCHITECTURE.md` | 系统边界、核心概念、Node/Service/Gateway 关系、权限与记忆模型 | 每个阶段的任务拆分和测试清单 |
| `DEVELOPMENT_PLAN.md` | 里程碑、排期、优先级、风险和降级策略 | 低层接口字段和逐项验收细节 |
| `SPEC_DRIVEN_DEVELOPMENT.md` | spec 开发流程、状态定义、变更规则和完成定义 | 具体模块的业务细节 |
| `specs/*.md` | 每个能力域的输入输出、不变量、验收证据和测试要求 | 长篇背景解释 |

## 当前需要优先补清楚的地方

- ReadGateway、Runtime Graph、Schema RAG、Memory/Artifact、WriteGateway 的边界必须按 spec 实现，不能只按计划标题自由发挥。
- 每个 spec 都需要固定数据结构、禁止项、错误语义、Trace 字段和测试证据。
- README 只做导航，不再承载完整方案；完整方案保留在 `ARCHITECTURE.md`。
