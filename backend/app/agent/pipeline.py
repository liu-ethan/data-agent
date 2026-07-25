from __future__ import annotations

import time
from collections.abc import Iterator

from app.agent.answer_composer import compose_answer
from app.agent.sql_executor import GuardrailError, execute_sql
from app.agent.sql_generator import generate_sql
from app.agent.state import AgentState
from app.api.schema import build_schema_tables
from app.security.sql_guardrail import check_sql


def iter_pipeline_events(state: AgentState) -> Iterator[tuple[str, dict]]:
    started = time.monotonic()
    yield (
        "run_start",
        {
            "request_id": state.request_id,
            "trace_id": state.trace_id,
            "session_id": state.session_id,
        },
    )

    try:
        yield ("node_start", {"node": "SQLGenerator"})
        schema_tables = build_schema_tables(state.user_role)
        state.generated_sql = generate_sql(
            state.question, schema_tables, state.user_role
        )
        yield ("node_end", {"node": "SQLGenerator", "summary": "sql_generated"})
        yield ("sql", {"sql": state.generated_sql, "repaired": False})

        yield ("node_start", {"node": "SQLGuardrail"})
        guard = check_sql(state.generated_sql, user_role=state.user_role)
        if not guard.ok:
            state.error = guard.reason or "SQL blocked by guardrail"
            yield ("node_end", {"node": "SQLGuardrail", "summary": "rejected"})
            yield ("error", {"message": state.error})
            state.latency_ms = int((time.monotonic() - started) * 1000)
            yield (
                "done",
                {
                    "latency_ms": state.latency_ms,
                    "need_clarification": False,
                    "clarification_question": None,
                },
            )
            return
        yield ("node_end", {"node": "SQLGuardrail", "summary": "passed"})

        yield ("node_start", {"node": "SQLExecutor"})
        state.columns, state.rows = execute_sql(
            state.generated_sql, user_role=state.user_role
        )
        yield ("node_end", {"node": "SQLExecutor", "summary": "executed"})
        yield (
            "rows",
            {"columns": state.columns, "rows": state.rows},
        )

        yield ("node_start", {"node": "AnswerComposer"})
        state.answer = compose_answer(
            state.question, state.columns, state.rows
        )
        yield ("node_end", {"node": "AnswerComposer", "summary": "composed"})
        yield ("answer", {"text": state.answer})

    except GuardrailError as exc:
        state.error = str(exc)
        yield ("error", {"message": state.error})
    except Exception as exc:
        state.error = str(exc)
        yield ("error", {"message": state.error})

    state.latency_ms = int((time.monotonic() - started) * 1000)
    yield (
        "done",
        {
            "latency_ms": state.latency_ms,
            "need_clarification": False,
            "clarification_question": None,
        },
    )
