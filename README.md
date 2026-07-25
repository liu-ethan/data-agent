# data-analysis-agent

[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=flat&logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=flat&logo=langchain&logoColor=white)](https://github.com/langchain-ai/langchain)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-20232A?style=flat&logo=react&logoColor=61DAFB)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

面向电商经营分析的自然语言数据分析 Agent。

用日常语言提问经营问题，Agent 理解意图、生成并安全执行查询，流式返回结论、表格与图表。支持多轮对话、角色权限与可解释的分析轨迹。

![Agent 架构](./assets/architecture.png)

---

## 目录

- [为什么不是普通 Text-to-SQL](#为什么不是普通-text-to-sql)
- [功能](#功能)
- [架构](#架构)
- [快速开始](#快速开始)
- [License](#license)

---

## 为什么不是普通 Text-to-SQL

| 普通 Demo | data-analysis-agent |
|-----------|-------------------|
| 一次 prompt 出 SQL | 状态图编排：可观测、可澄清、可修复 |
| 模型直连数据库 | 权限校验 + 沙箱执行，角色差异化 |
| 同步黑盒结果 | 流式展示分析过程与结果 |
| 无账号体系 | 登录注册；分析师 / 管理员角色 |
| 无治理 | 工具注册与审计日志 |

---

## 功能

- **自然语言经营分析**：GMV、退款率、转化率、渠道 TopN 等
- **双角色权限**：分析师只读；管理员受控可写（需邀请码注册）
- **流式工作台**：实时展示分析轨迹、SQL、结果表、自动图表与结论
- **多轮记忆**：会话内槽位延续；跨会话保留偏好与近期分析摘要
- **问题澄清**：意图不清时先追问，不盲目查数
- **安全执行**：查询经权限校验与沙箱，敏感字段与危险操作受控

---

## 架构

```text
登录 / 注册  →  工作台提问
                    │
                    ▼
         ┌──── Agent 编排 ────┐
         │  理解意图 · 澄清    │
         │  简单 / 复杂分流    │
         │  安全校验与执行     │
         │  图表规划 · 作答    │
         └─────────┬──────────┘
                   │
                   ▼
              业务数据库
```

前端提供营销登录页与分析工作台；后端以 Agent 状态图编排分析流程，查询统一经权限与沙箱后再访问数据。

---

## 快速开始

### 1. 配置

```bash
cp config_template.yaml config.yaml
```

在 `config.yaml` 中填写模型 API（`llm`）以及 `backend.jwt_secret`、`backend.admin_invite_code`。  
`config.yaml` 含密钥，不要提交到仓库。

### 2. 后端

需 Python 3.12（推荐 conda 环境 `python3.12`）：

```bash
cd backend
pip install -r requirements.txt
python -m app.db.init_db
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 前端

另开终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 [http://localhost:5173](http://localhost:5173)：

1. 注册分析师账号，或使用种子账号 `demo_analyst` / `demo1234`
2. 注册管理员时填写 `config.yaml` 中的邀请码
3. 登录后进入工作台，用自然语言提问

### 4. 试一试

- 上个月 GMV 最高的 5 个渠道是什么？
- 最近 30 天每天的订单量和 GMV 趋势如何？
- 哪些商品品类的退款率最高？
- 各城市的新用户注册数排名如何？
- 不同支付方式的支付成功率是多少？

---

## License

本项目采用 [MIT License](./LICENSE) 开源。
