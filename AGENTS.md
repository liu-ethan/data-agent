# Agent 工作约定

按 `docs/` 重构本项目。`docs/ARCHITECTURE.md` 解释设计，`docs/specs/` 是实现依据。旧 Codex 代码若冗余、过复杂或不符合文档，直接删掉重写，不要迁就现状。

## 工作方式

1. 严格按 `docs/specs/` 编号顺序推进，一次只做一个 spec。
2. 做完当前 spec（实现 + 测试通过 + 相关前后端可运行）后立刻停下，等我验证。未确认前不要开始下一个。
3. Spec 边界不清时先改 spec，再改代码。
4. 最终交付必须是可运行的前后端系统，不是半成品或到处是 bug 的代码。

## 实现原则

- 先确定性边界，再接 LLM。
- 数据库访问只走 Gateway / Repository，禁止 Node 直连。
- 只为实现当前 spec 改代码；不为旧架构保留旁路。
- 不需要帮我git commit和push，我自己检查过后会push

## 开发环境

- Python: `python3.12`（conda env，prefix `/home/user/miniconda3/envs/python3.12`）
- Milvus: `pymilvus==2.6.14` + `milvus==2.3.5`（Embedded Milvus Lite）
