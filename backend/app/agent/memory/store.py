from __future__ import annotations

import json
from datetime import datetime

from app.agent.memory.summarize import merge_preferences, strip_sensitive
from app.db.database import get_connection

MAX_TURNS_PER_SESSION = 10
MAX_SUMMARIES_PER_USER = 20


class MemoryError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _user_id(user_id: str) -> int:
    return int(str(user_id))


def _redact_value(value):
    if isinstance(value, str):
        return strip_sensitive(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _json(value) -> str:
    return json.dumps(_redact_value(value), ensure_ascii=False)


def ensure_session(session_id: str, user_id: str) -> None:
    owner_id = _user_id(user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        ).fetchone()
        if row is None:
            now = _now()
            conn.execute(
                """
                INSERT INTO chat_sessions (id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(session_id), owner_id, None, now, now),
            )
            conn.commit()
        elif row["user_id"] != owner_id:
            raise MemoryError("Session belongs to another user")
    finally:
        conn.close()


def load_last_turn_slots(session_id: str, user_id: str) -> dict | None:
    ensure_session(session_id, user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT question, intent, sql_text, metrics_json, time_range_json,
                   filters_json, group_by_json, result_summary
            FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index DESC
            LIMIT 1
            """,
            (str(session_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "metrics": json.loads(row["metrics_json"] or "[]"),
        "time_range": json.loads(row["time_range_json"] or "null"),
        "group_by": json.loads(row["group_by_json"] or "[]"),
        "filters": json.loads(row["filters_json"] or "{}"),
        "last_sql": row["sql_text"],
        "last_question": row["question"],
        "last_intent": row["intent"],
        "last_result_summary": row["result_summary"],
    }


def load_preferences(user_id: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT preferences_json FROM user_preferences WHERE user_id = ?",
            (_user_id(user_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["preferences_json"]:
        return {}
    return json.loads(row["preferences_json"])


def load_recent_summaries(user_id: str, *, limit: int = 5) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, user_id, session_id, question_summary, answer_summary,
                   metrics_json, filters_json, created_at
            FROM user_analysis_summaries
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (_user_id(user_id), max(0, int(limit))),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "user_id": str(row["user_id"]),
            "session_id": row["session_id"],
            "question_summary": row["question_summary"],
            "answer_summary": row["answer_summary"],
            "metrics": json.loads(row["metrics_json"] or "[]"),
            "filters": json.loads(row["filters_json"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def save_turn(
    *,
    session_id: str,
    user_id: str,
    question: str,
    intent: str,
    sql_text: str | None,
    slots: dict,
    result_summary: str,
) -> None:
    ensure_session(session_id, user_id)
    conn = get_connection()
    try:
        turn_index = conn.execute(
            """
            SELECT COALESCE(MAX(turn_index), 0) + 1
            FROM session_turns
            WHERE session_id = ?
            """,
            (str(session_id),),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO session_turns (
                session_id, turn_index, question, intent, sql_text,
                metrics_json, time_range_json, filters_json, group_by_json,
                result_summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session_id),
                turn_index,
                strip_sensitive(question),
                strip_sensitive(intent),
                strip_sensitive(sql_text) if sql_text is not None else None,
                _json(slots.get("metrics") or []),
                _json(slots.get("time_range")),
                _json(slots.get("filters") or {}),
                _json(slots.get("group_by") or []),
                strip_sensitive(result_summary),
                _now(),
            ),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM session_turns WHERE session_id = ?",
            (str(session_id),),
        ).fetchone()[0]
        excess = count - MAX_TURNS_PER_SESSION
        if excess > 0:
            conn.execute(
                """
                DELETE FROM session_turns
                WHERE id IN (
                    SELECT id
                    FROM session_turns
                    WHERE session_id = ?
                    ORDER BY turn_index ASC
                    LIMIT ?
                )
                """,
                (str(session_id), excess),
            )
        conn.commit()
    finally:
        conn.close()


def update_preferences_from_slots(user_id: str, slots: dict) -> None:
    owner_id = _user_id(user_id)
    preferences = merge_preferences(load_preferences(str(user_id)), slots)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO user_preferences (user_id, preferences_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                preferences_json = excluded.preferences_json,
                updated_at = excluded.updated_at
            """,
            (owner_id, _json(preferences), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def append_summary(
    *,
    user_id: str,
    session_id: str,
    question_summary: str,
    answer_summary: str,
    metrics: list,
    filters: dict,
) -> None:
    ensure_session(session_id, user_id)
    owner_id = _user_id(user_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO user_analysis_summaries (
                user_id, session_id, question_summary, answer_summary,
                metrics_json, filters_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                str(session_id),
                strip_sensitive(question_summary),
                strip_sensitive(answer_summary),
                _json(metrics),
                _json(filters),
                _now(),
            ),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM user_analysis_summaries WHERE user_id = ?",
            (owner_id,),
        ).fetchone()[0]
        excess = count - MAX_SUMMARIES_PER_USER
        if excess > 0:
            conn.execute(
                """
                DELETE FROM user_analysis_summaries
                WHERE id IN (
                    SELECT id
                    FROM user_analysis_summaries
                    WHERE user_id = ?
                    ORDER BY id ASC
                    LIMIT ?
                )
                """,
                (owner_id, excess),
            )
        conn.commit()
    finally:
        conn.close()
