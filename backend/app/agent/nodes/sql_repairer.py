from __future__ import annotations

import json
import re

from app.agent.llm import chat_completion
from app.agent.state import AgentState

_FENCE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.I)


def _extract_sql(text: str) -> str:
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    if m:
        return m.group(1).strip().rstrip(";")
    return raw.strip().rstrip(";")


def sql_repairer(state: AgentState) -> dict:
    question = state.get("question") or ""
    sql = state.get("generated_sql") or ""
    err = state.get("error") or ""
    schema = {
        "tables": state.get("relevant_tables") or [],
        "columns": state.get("relevant_columns") or {},
        "metrics": state.get("metric_specs") or [],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是 SQLite SQL 修复器。根据错误修复 SQL。"
                "只输出一条 SQL，不要解释。不要 DDL。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\nSQL:\n{sql}\n\nError:\n{err}\n\n"
                f"Schema:\n{json.dumps(schema, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        fixed = _extract_sql(chat_completion(messages))
        if not fixed:
            return {
                "repaired": True,
                "error": err or "SQL repair produced empty SQL",
            }
        return {"repaired": True, "generated_sql": fixed, "error": None}
    except Exception:
        return {
            "repaired": True,
            "error": err or "SQL repair failed",
        }
