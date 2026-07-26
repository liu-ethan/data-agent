from __future__ import annotations

import json
import re

from app.prompts import render


def _extract_sql(text: str) -> str:
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def generate_sql(
    question: str,
    relevant_tables_schema: list,
    metric_specs: list[dict],
    slots: dict,
    user_role: str,
) -> str:
    from app.agent.llm import chat_completion

    _ = user_role
    schema_json = json.dumps(
        relevant_tables_schema, ensure_ascii=False, indent=2
    )
    metric_specs_json = json.dumps(metric_specs, ensure_ascii=False, indent=2)
    slots_json = json.dumps(slots, ensure_ascii=False, indent=2)
    parts = render(
        "sql_generator",
        schema_json=schema_json,
        metric_specs_json=metric_specs_json,
        slots_json=slots_json,
        question=question,
    )
    raw = chat_completion(
        [
            {"role": "system", "content": parts["system"]},
            {"role": "user", "content": parts["user"]},
        ]
    )
    return _extract_sql(raw)
