from __future__ import annotations

import json


def compose_answer(question: str, columns: list[str], rows: list[dict]) -> str:
    from app.agent.llm import chat_completion

    try:
        sample = rows[:20]
        payload = json.dumps(
            {"columns": columns, "rows": sample, "total_rows": len(rows)},
            ensure_ascii=False,
        )
        return chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize the SQL query result in concise Chinese for the user. "
                        "Do not invent numbers not present in the data."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\nResult JSON:\n{payload}",
                },
            ]
        )
    except Exception:
        return f"查询返回 {len(rows)} 行。"
