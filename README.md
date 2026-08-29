# data-agent

电商经营分析数据 Agent：单 Coordinator + 查询 Skill + 写入 Skill。

需要 Python 3.12。

## 安装

```bash
pip install -e .
cp config.example.yaml config.yaml
```

在 `config.yaml` 中填入本机 MySQL / LLM / Embedding 配置。不要把该文件提交进仓库。

Prompt 在 `prompts/`，SQL 在 `sql/`，指标/切片/写入模板在 `seeds/`。不要把这些内容再抄进 Python。

## 启动

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend && npm install && npm run dev
```

## 测试

```bash
pytest tests -v
```
