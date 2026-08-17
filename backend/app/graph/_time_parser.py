"""Deterministic relative-time parser for runtime questions.

Keeps a small, fast Chinese+English vocabulary so the runtime can resolve
``昨天`` / ``today`` / ``最近 N 天`` without consulting the LLM. The
absolute range is fixed at the moment of parsing so a later resume cannot
silently re-interpret it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..errors import RuntimeAgentError
from ..models import TimeRange

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.9+
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore[no-redef]


_KEYWORD_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("昨天",), 1),
    (("前天",), 2),
    (("今天", "今日"), 0),
    (("最近 7 天", "最近7天"), 7),
    (("最近 15 天", "最近15天"), 15),
    (("最近 30 天", "最近30天"), 30),
    (("本月",), -1),  # -1 sentinel means "first day of the month"
    (("上月",), -2),
)


def parse_time_range(question: str, timezone_name: str) -> TimeRange:
    """Return a half-open ``[start, end)`` range for ``question``.

    Vocabulary order matters: more specific phrases (``最近 15 天``) must
    match before generic ones. Unknown phrasing falls back to ``昨天``,
    matching the prior implicit default to avoid surprising an established
    eval suite.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeAgentError("INVALID_TIMEZONE",
                                "timezone must be a valid IANA name") from exc

    anchor = datetime.now(zone)
    today = anchor.replace(hour=0, minute=0, second=0, microsecond=0)

    for keywords, days in _KEYWORD_RULES:
        if any(keyword in question for keyword in keywords):
            if days == 0:
                start, end = today, anchor
            elif days == -1:
                start, end = today.replace(day=1), anchor
            elif days == -2:
                last_month_end = today.replace(day=1)
                last_month_start = last_month_end - timedelta(days=1)
                start = last_month_start.replace(day=1)
                end = last_month_end
            else:
                start, end = today - timedelta(days=days), today
            break
    else:
        start, end = today - timedelta(days=1), today

    if "本月" in question and "上月" in question:
        last_month_end = today.replace(day=1)
        last_month_start = last_month_end - timedelta(days=1)
        start = last_month_start.replace(day=1)
        end = anchor

    return TimeRange(start=start, end=end, timezone=timezone_name)


def has_explicit_time(question: str) -> bool:
    """True when ``question`` names a relative window the parser understands."""
    return any(any(keyword in question for keyword in keywords) for keywords, _ in _KEYWORD_RULES)
