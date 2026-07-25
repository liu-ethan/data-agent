from __future__ import annotations

import json
import re


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
    system = (
        "You are a SQLite analyst assistant. Generate exactly one read-only SQL query "
        "(SELECT or WITH only) that answers the user's question.\n"
        f"Relevant tables and columns (JSON):\n{schema_json}\n"
        f"Metric specifications (JSON):\n{metric_specs_json}\n"
        f"Parsed slots (JSON):\n{slots_json}\n"
        "Reply with the SQL only, or wrap it in a ```sql fenced block."
    )
    raw = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
    )
    return _extract_sql(raw)
