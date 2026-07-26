from __future__ import annotations

import json
import re
from datetime import datetime

from app.agent.llm import chat_completion

_CHART_TYPES = frozenset({"line", "bar", "pie", "table"})
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_SAMPLE_ROWS = 12
_DATE_NAME = re.compile(r"(date|time|日|天|_at|_on)", re.IGNORECASE)
_SHARE_NAME = re.compile(r"(rate|ratio|占比|比例|份额|percent)", re.IGNORECASE)
_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S")


def plan_chart(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    slots: dict | None = None,
    title_hint: str = "",
) -> dict | None:
    if not columns or not rows:
        return None
    sample = rows[:_SAMPLE_ROWS]
    if question.strip():
        try:
            raw = chat_completion(
                _build_messages(question, columns, sample, slots, title_hint)
            )
            parsed = _parse_json(raw)
            validated = _validate_chart(parsed, columns, sample)
            if validated is not None:
                return _enrich_line_series(validated, columns, sample)
        except Exception:
            pass
    return _enrich_line_series(
        _heuristic_chart(question, columns, sample, title_hint),
        columns,
        sample,
    )


def _build_messages(
    question: str,
    columns: list[str],
    sample: list[dict],
    slots: dict | None,
    title_hint: str,
) -> list[dict]:
    payload = {
        "question": question,
        "columns": columns,
        "sample_rows": sample,
        "slots": slots,
        "title_hint": title_hint,
    }
    system = (
        "你是图表规划器。根据查询结果选择图表，只输出 JSON 对象，字段："
        'type(line|bar|pie|table), x(列名), y(列名), title(短中文), '
        "series(可选，数值列名数组，多指标趋势时填写)。"
        "趋势用 line，TopN/分类对比用 bar，占比用 pie，明细用 table。"
        "x/y 必须是 columns 中的列名；多指标趋势（如订单量+GMV）用 line 并填 series。"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _validate_chart(
    data: dict | None, columns: list[str], sample: list[dict]
) -> dict | None:
    if not data:
        return None
    ctype = str(data.get("type") or "").strip().lower()
    if ctype not in _CHART_TYPES:
        return None
    x = str(data.get("x") or "")
    y = str(data.get("y") or "")
    title = str(data.get("title") or "")
    if ctype == "table":
        return {
            "type": "table",
            "x": x if x in columns else (columns[0] if columns else ""),
            "y": y if y in columns else (columns[1] if len(columns) > 1 else ""),
            "title": title,
        }
    if x not in columns or y not in columns:
        return None
    if not _column_mostly_numeric(sample, y):
        return None
    series = _normalize_series(data.get("series"), columns, sample, y)
    out = {"type": ctype, "x": x, "y": y, "title": title}
    if series:
        out["series"] = series
    return out


def _heuristic_chart(
    question: str,
    columns: list[str],
    sample: list[dict],
    title_hint: str,
) -> dict:
    title = title_hint or question[:40]
    date_col = _find_date_column(columns, sample)
    num_cols = [c for c in columns if _column_mostly_numeric(sample, c)]
    cat_cols = [c for c in columns if c not in num_cols]
    if date_col and num_cols:
        y_cols = [c for c in num_cols if c != date_col]
        if y_cols:
            chart = {"type": "line", "x": date_col, "y": y_cols[0], "title": title}
            if len(y_cols) > 1:
                chart["series"] = y_cols
            return chart
    share_cols = [c for c in num_cols if _SHARE_NAME.search(c)]
    if share_cols and cat_cols:
        y = share_cols[0]
        x = next((c for c in cat_cols if c != y), cat_cols[0])
        distinct = {row.get(x) for row in sample}
        ctype = "pie" if len(distinct) <= 12 else "bar"
        return {"type": ctype, "x": x, "y": y, "title": title}
    if (
        _SHARE_NAME.search(question)
        and cat_cols
        and num_cols
        and len({row.get(cat_cols[0]) for row in sample}) <= 12
    ):
        return {
            "type": "pie",
            "x": cat_cols[0],
            "y": num_cols[0],
            "title": title,
        }
    if cat_cols and num_cols:
        return {
            "type": "bar",
            "x": cat_cols[0],
            "y": num_cols[0],
            "title": title,
        }
    return {
        "type": "table",
        "x": columns[0] if columns else "",
        "y": columns[1] if len(columns) > 1 else "",
        "title": title,
    }


def _enrich_line_series(
    chart: dict, columns: list[str], sample: list[dict]
) -> dict:
    if chart.get("type") != "line":
        return chart
    x = str(chart.get("x") or "")
    num_cols = [
        c
        for c in columns
        if c != x and _column_mostly_numeric(sample, c)
    ]
    if len(num_cols) <= 1:
        return chart
    out = dict(chart)
    if not out.get("series"):
        out["series"] = num_cols
    if out.get("y") not in num_cols:
        out["y"] = num_cols[0]
    return out


def _normalize_series(
    raw: object,
    columns: list[str],
    sample: list[dict],
    primary_y: str,
) -> list[str]:
    if not isinstance(raw, list):
        return []
    series: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if (
            name
            and name in columns
            and name not in series
            and _column_mostly_numeric(sample, name)
        ):
            series.append(name)
    if primary_y not in series and primary_y in columns:
        series.insert(0, primary_y)
    return series if len(series) > 1 else []


def _find_date_column(columns: list[str], sample: list[dict]) -> str | None:
    for c in columns:
        if _DATE_NAME.search(c):
            return c
    for c in columns:
        values = [row.get(c) for row in sample if row.get(c) is not None]
        if values and all(_looks_date(v) for v in values[:5]):
            return c
    return None


def _looks_date(value: object) -> bool:
    if isinstance(value, datetime):
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    for fmt in _DATE_FMTS:
        try:
            datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt)
            return True
        except ValueError:
            continue
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return True
    return False


def _column_mostly_numeric(sample: list[dict], col: str) -> bool:
    vals = [row.get(col) for row in sample if row.get(col) is not None]
    if not vals:
        return False
    ok = sum(1 for v in vals if _is_number(v))
    return ok * 2 >= len(vals)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False
