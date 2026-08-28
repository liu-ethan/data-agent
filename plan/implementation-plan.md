# data-agent 从 0 到 1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **先读** [development-notes.md](development-notes.md)（不变量、存储边界、核心契约、禁止项）。与 Task 文件冲突时以那份为准。

**Goal:** 从空仓库实现一个面向电商经营分析的可信数据 Agent：单 Coordinator + 可信查询 Skill + 受控写入 Skill，能安全问数、受限多轮、小范围写入，并用固定评测集验证。

本文件只做索引。通用边界与注意事项不在这里重复。

---

## 必读

| 文件 | 用途 |
| --- | --- |
| [development-notes.md](development-notes.md) | **通用开发注意事项与边界**。Locked Decisions、三套存储、模块所有权、核心契约、文件地图、MVP 不做。实现任何 Task 前必读。 |
| [config-needed.md](config-needed.md) | 配置清单（已写入根目录 `config.yaml`） |

---

## Tasks

按依赖顺序执行。每个文件含该 Task 的 **Boundary**（Owns / In / Out / Must not）。

| # | 文件 | 里程碑 |
| --- | --- | --- |
| 1 | [tasks/01-skeleton.md](tasks/01-skeleton.md) | M0 工程骨架 + config |
| 2 | [tasks/02-runtime.md](tasks/02-runtime.md) | M1 类型 / 时间 / 权限 |
| 3 | [tasks/03-mysql-pool.md](tasks/03-mysql-pool.md) | M1 MySQL 连接池 |
| 4 | [tasks/04-catalog.md](tasks/04-catalog.md) | M1 Catalog / 指标 / 关系 |
| 5 | [tasks/05-read-gateway.md](tasks/05-read-gateway.md) | M2 只读 SQL 网关 |
| 6 | [tasks/06-result-store.md](tasks/06-result-store.md) | M2 Result Store |
| 7 | [tasks/07-execute-read.md](tasks/07-execute-read.md) | M2 只读执行 |
| 8 | [tasks/08-schema-rag.md](tasks/08-schema-rag.md) | M3 Schema RAG |
| 9 | [tasks/09-metric-compiler.md](tasks/09-metric-compiler.md) | M3 MetricCompiler |
| 10 | [tasks/10-query-skill.md](tasks/10-query-skill.md) | M3 可信查询 Skill |
| 11 | [tasks/11-write-gateway.md](tasks/11-write-gateway.md) | M4 写入网关 / 事务 |
| 12 | [tasks/12-write-skill.md](tasks/12-write-skill.md) | M4 受控写入 Skill |
| 13 | [tasks/13-coordinator.md](tasks/13-coordinator.md) | M4/M5 Coordinator |
| 14 | [tasks/14-http-api.md](tasks/14-http-api.md) | M5 HTTP API |
| 15 | [tasks/15-frontend.md](tasks/15-frontend.md) | M5 前端工作台 |
| 16 | [tasks/16-eval.md](tasks/16-eval.md) | M6 评测 |

依赖图与规格覆盖表见 [development-notes.md](development-notes.md) §11–§12。
