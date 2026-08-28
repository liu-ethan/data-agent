from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from backend.app.types import TimeRange


def _aware_utc(request_time_utc: str) -> datetime:
    raw = request_time_utc.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("request_time_utc must be timezone-aware")
    return dt


def _local_day_start(request_time_utc: str, timezone: str) -> datetime:
    local = _aware_utc(request_time_utc).astimezone(ZoneInfo(timezone))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_month(start: datetime) -> datetime:
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def resolve_time_range(text: str | None, request_time_utc: str, timezone: str) -> TimeRange:
    """Parse relative time with the given request instant. Never reads wall clock."""
    today = _local_day_start(request_time_utc, timezone)
    key = (text or "").strip()
    source: Literal["user", "server_default"] = "server_default" if not key else "user"

    if not key or key in ("今天", "今日"):
        start = today
        end = today + timedelta(days=1)
        grain = "day"
        label = start.date().isoformat()
    elif key in ("昨天", "昨日"):
        start = today - timedelta(days=1)
        end = today
        grain = "day"
        label = start.date().isoformat()
    elif key == "本月":
        start = today.replace(day=1)
        end = _next_month(start)
        grain = "month"
        label = f"{start.year:04d}-{start.month:02d}"
    elif key in ("近7天", "最近7天", "过去7天"):
        start = today - timedelta(days=6)
        end = today + timedelta(days=1)
        grain = "day"
        label = f"{start.date().isoformat()}~{today.date().isoformat()}"
    else:
        raise ValueError(f"unrecognized time range: {text!r}")

    return TimeRange(
        start=start.isoformat(),
        end=end.isoformat(),
        grain=grain,
        label=label,
        source=source,
    )
