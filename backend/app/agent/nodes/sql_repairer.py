from __future__ import annotations

import json
import re

from app.agent.llm import chat_completion
from app.agent.state import AgentState
from app.prompts import render

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
    parts = render(
        "sql_repairer",
        question=question,
        sql=sql,
        error=err,
        schema_json=json.dumps(schema, ensure_ascii=False),
    )
    messages = [
        {"role": "system", "content": parts["system"]},
        {"role": "user", "content": parts["user"]},
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
