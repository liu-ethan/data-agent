from __future__ import annotations

import json

from app.agent.llm import chat_completion


def compose_answer(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    is_write: bool = False,
    affected_rows: int | None = None,
) -> str:
    if is_write:
        n = affected_rows if affected_rows is not None else 0
        return f"写操作已成功执行，影响 {n} 行。"

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
