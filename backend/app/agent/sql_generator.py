from __future__ import annotations

import json
import re


def _extract_sql(text: str) -> str:
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def generate_sql(question: str, schema_tables: list, user_role: str) -> str:
    from app.agent.llm import chat_completion

    _ = user_role
    schema_json = json.dumps(schema_tables, ensure_ascii=False, indent=2)
    system = (
        "You are a SQLite analyst assistant. Generate exactly one read-only SQL query "
        "(SELECT or WITH only) that answers the user's question.\n"
        f"Available tables and columns (JSON):\n{schema_json}\n"
        "Reply with the SQL only, or wrap it in a ```sql fenced block."
    )
    raw = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
    )
    return _extract_sql(raw)
