from __future__ import annotations

import json

from app.agent.llm import chat_completion
from app.prompts import render


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
        result_json = json.dumps(
            {"columns": columns, "rows": sample, "total_rows": len(rows)},
            ensure_ascii=False,
        )
        parts = render(
            "answer_composer",
            question=question,
            result_json=result_json,
        )
        return chat_completion(
            [
                {"role": "system", "content": parts["system"]},
                {"role": "user", "content": parts["user"]},
            ]
        )
    except Exception:
        return f"查询返回 {len(rows)} 行。"
