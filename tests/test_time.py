from backend.app.runtime.time import resolve_time_range


def test_today_is_half_open_interval():
    tr = resolve_time_range("今天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    # Asia/Shanghai = UTC+8, 16:00 UTC = 00:00 next calendar day
    assert tr.start.startswith("2026-08-29") or tr.start.startswith("2026-08-28")
    assert tr.start == "2026-08-29T00:00:00+08:00"
    assert tr.end == "2026-08-30T00:00:00+08:00"
    assert tr.end > tr.start
    assert tr.grain == "day"
    assert tr.source == "user"
    assert tr.label == "2026-08-29"


def test_this_month_exclusive_end():
    tr = resolve_time_range("本月", "2026-08-28T03:00:00+00:00", "Asia/Shanghai")
    assert tr.start.startswith("2026-08-01")
    assert tr.end.startswith("2026-09-01")
    assert tr.start == "2026-08-01T00:00:00+08:00"
    assert tr.end == "2026-09-01T00:00:00+08:00"
    assert tr.grain == "month"
    assert tr.source == "user"
    assert tr.label == "2026-08"


def test_yesterday_is_previous_local_day():
    tr = resolve_time_range("昨天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    assert tr.start == "2026-08-28T00:00:00+08:00"
    assert tr.end == "2026-08-29T00:00:00+08:00"
    assert tr.grain == "day"
    assert tr.label == "2026-08-28"


def test_last_7_days_includes_today():
    tr = resolve_time_range("近7天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    assert tr.start == "2026-08-23T00:00:00+08:00"
    assert tr.end == "2026-08-30T00:00:00+08:00"
    assert tr.grain == "day"


def test_missing_text_defaults_to_today_from_request_time():
    tr = resolve_time_range(None, "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    assert tr.start == "2026-08-29T00:00:00+08:00"
    assert tr.end == "2026-08-30T00:00:00+08:00"
    assert tr.source == "server_default"
    assert tr.grain == "day"


def test_same_request_time_is_deterministic():
    a = resolve_time_range("今天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    b = resolve_time_range("今天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    assert a == b
