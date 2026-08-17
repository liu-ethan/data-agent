import asyncio
import json
import threading
from dataclasses import dataclass

import pytest

from backend.app.config import load_settings
from backend.app.graph.nodes import agent_node
from backend.app.graph.state import (
    AnswerDraft,
    ConversationalAnswerDraft,
    QueryDraft,
    TaskUnderstanding,
)
from backend.app.models import AgentState, PermissionContext, RunStatus
from backend.app.repositories.runtime import RuntimePersistence
from backend.app.testing import build_test_permission, build_test_runtime


@dataclass(frozen=True)
class ScriptTrace:
    provider: str = "scripted"
    protocol: str = "anthropic"
    model: str = "contract-test-model"
    duration_ms: float = 1.0
    input_tokens: int = 10
    output_tokens: int = 5
    total_tokens: int = 15
    attempt_count: int = 1


class ScriptedLLM:
    def __init__(self):
        self.calls = []

    async def structured(self, *, schema, purpose, user, prompt_version, **kwargs):
        self.calls.append((purpose, prompt_version, schema.__name__))
        if schema is TaskUnderstanding:
            return TaskUnderstanding(
                task_type="DATA_QUERY", metric_ids=["category_gmv"],
                dimension_ids=["categories.category_name"],
                mentions={"metric": ["GMV"], "dimension": ["品类"]}), ScriptTrace()
        if schema is QueryDraft:
            task = json.loads(user)["task"]
            # time_range dates are serialized as ISO strings; the runtime
            # expects MySQL DATETIME placeholders ("YYYY-MM-DD HH:MM:SS").
            start = task["time_range"]["start"].split("T")[0] + " 00:00:00"
            end = task["time_range"]["end"].split("T")[0] + " 00:00:00"
            return QueryDraft(
                status="QUERY_PLAN",
                candidate_sql=(
                    "SELECT c.category_name AS category_name, "
                    "SUM(oi.item_paid_amount) AS category_gmv FROM orders o "
                    "JOIN order_items oi ON oi.order_id=o.order_id "
                    "JOIN products p ON p.product_id=oi.product_id "
                    "JOIN categories c ON c.category_id=p.category_id "
                    "WHERE o.status=:status AND o.paid_at>=:start "
                    "AND o.paid_at<:end GROUP BY c.category_id,c.category_name "
                    "ORDER BY category_gmv DESC LIMIT :max_rows"),
                parameters={"status": "PAID",
                            "start": start,
                            "end": end,
                            "max_rows": 1000},
                metric_refs=["category_gmv"],
                dimension_refs=["categories.category_name"],
                expected_columns=["category_name", "category_gmv"],
                time_field="orders.paid_at",
                required_object_ids=["obj_orders", "obj_order_items",
                                     "obj_products", "obj_categories"],
            ), ScriptTrace()
        if schema is AnswerDraft:
            result_id = json.loads(user)["result_id"]
            return AnswerDraft(
                answer="查询完成，各品类 GMV 已按降序返回。",
                evidence_result_ids=[result_id]), ScriptTrace()
        raise AssertionError(schema)


class ConversationalLLM:
    def __init__(self):
        self.calls = []

    async def structured(self, *, schema, purpose, prompt_version, **kwargs):
        self.calls.append((purpose, prompt_version, schema.__name__))
        if schema is TaskUnderstanding:
            return TaskUnderstanding(
                task_type="CHAT_OR_OUT_OF_SCOPE", next_action="RESPOND",
                deliverables=["TEXT"], mentions={"raw": ["nihao1"]},
            ), ScriptTrace()
        if schema is ConversationalAnswerDraft:
            return ConversationalAnswerDraft(
                answer="你好！你可以问我昨天的 GMV，或者查询业务表字段。",
            ), ScriptTrace()
        raise AssertionError(schema)


def test_single_turn_contains_execute_and_returns_result():
    graph = build_test_runtime(settings=load_settings().raw)
    response = graph.run(message="昨天各品类 GMV 是多少？", user_id="u_demo_user",
                         permission=build_test_permission("u_demo_user"))
    assert response.status.value == "SUCCEEDED"
    actions = [event["action"] for event in response.events if event.get("event") == "node.completed"]
    assert actions[:4] == ["RETRIEVE", "RETRIEVE", "GENERATE", "GENERATE"] or "EXECUTE" in actions
    assert "EXECUTE" in actions
    assert response.result_ids
    assert graph.gateway.results.get(response.result_ids[0]) == [
        {"category_name": "手机通讯", "gmv": 948.0},
        {"category_name": "厨房用品", "gmv": 780.0},
        {"category_name": "电脑周边", "gmv": 351.0},
    ]


def test_schema_question_does_not_execute_sql():
    graph = build_test_runtime(settings=load_settings().raw)
    response = graph.run(message="orders 表有哪些字段？", user_id="u_demo_user",
                         permission=build_test_permission("u_demo_user"))
    assert response.status.value == "SUCCEEDED"
    assert not response.result_ids
    assert "orders." in (response.answer or "")


def test_today_time_range_and_driver_placeholders_are_canonicalized():
    from backend.app.graph._sql_canonicalizer import canonicalize_parameters
    from backend.app.graph._time_parser import parse_time_range
    resolved = parse_time_range("再查今天的", "Asia/Shanghai")
    assert resolved.start.date() == resolved.end.date()
    assert str(resolved.start.tzinfo) == "Asia/Shanghai"
    utc = parse_time_range("再查今天的", "UTC")
    assert str(utc.start.tzinfo) == "UTC"
    with pytest.raises(Exception, match="IANA"):
        parse_time_range("昨天", "Mars/Olympus")
    assert canonicalize_parameters(
        "SELECT 1 FROM orders WHERE paid_at >= %(start_ts)s", {"start_ts": "x"}
    ).endswith("paid_at >= :start_ts")


def test_agent_iteration_budget_is_enforced():
    graph = build_test_runtime(settings={"runtime_agent": {"max_iterations": 1}})
    state = AgentState(thread_id="t", request_id="r", user_id="u",
                       budgets={"iterations_used": 1})
    run = {"state": state, "message": "昨天 GMV", "timezone_name": "Asia/Shanghai",
           "permission": PermissionContext(user_id="u", scope_mode="ALLOWLIST",
                                           allowed_shop_ids=["shop_001"], policy_version="p")}
    asyncio.run(agent_node(graph, run))
    assert state.status == RunStatus.TIMEOUT
    assert state.budgets["iterations_used"] == 2
    assert any(event.get("error_code") == "BUDGET_EXCEEDED" for event in state.action_history)


def test_llm_agent_runs_typed_grounded_query_and_evidence_bound_answer():
    llm = ScriptedLLM()
    graph = build_test_runtime(settings=load_settings().raw, llm=llm)
    response = graph.run(
        message="昨天各品类 GMV 是多少？", user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
    )
    assert response.status == RunStatus.SUCCEEDED
    assert response.answer == "查询完成，各品类 GMV 已按降序返回。"
    assert response.result_ids
    assert llm.calls == [
        ("agent", "task_understanding_v4", "TaskUnderstanding"),
        ("query_generation", "query_generation_v1", "QueryDraft"),
        ("response", "response_v1", "AnswerDraft"),
    ]
    completed = next(event for event in response.events
                     if event["event"] == "run.completed")
    assert completed["model_usage"] == {
        "models": ["contract-test-model"],
        "input_tokens": 30,
        "output_tokens": 15,
        "model_duration_ms": 3.0,
    }


def test_open_ended_input_uses_llm_conversational_fallback_without_schema_or_sql():
    llm = ConversationalLLM()
    graph = build_test_runtime(settings=load_settings().raw, llm=llm)
    response = graph.run(
        message="nihao1", user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
    )
    assert response.status == RunStatus.SUCCEEDED
    assert response.answer.startswith("你好！")
    assert not response.result_ids
    actions = [event.get("action") for event in response.events
               if event.get("event") == "node.completed"]
    assert "RETRIEVE" not in actions
    assert "GENERATE" not in actions
    assert "EXECUTE" not in actions
    assert llm.calls == [
        ("agent", "task_understanding_v4", "TaskUnderstanding"),
        ("conversational_response", "conversational_response_v1",
         "ConversationalAnswerDraft"),
    ]


def test_langgraph_persists_sse_events_before_the_run_finishes(tmp_path):
    """A blocked gateway must not delay already completed SSE graph events."""
    async def scenario():
        graph = build_test_runtime(settings=load_settings().raw)
        graph.persistence = RuntimePersistence(
            url=f"sqlite:///{tmp_path / 'live-events.db'}", create_schema=True)
        delegate = graph.gateway
        entered = threading.Event()
        release = threading.Event()

        class SlowGateway:
            def execute(self, plan, permission):
                entered.set()
                if not release.wait(timeout=3):
                    raise TimeoutError("test did not release the gateway")
                return delegate.execute(plan, permission)

        graph.gateway = SlowGateway()
        request_id = "req_live_sse"
        task = asyncio.create_task(graph.arun(
            message="昨天各品类 GMV 是多少？",
            user_id="u_demo_user",
            permission=build_test_permission("u_demo_user"),
            request_id=request_id,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            visible = graph.persistence.events_after(request_id, "u_demo_user")
            assert not task.done()
            assert visible[0][1]["event"] == "run.started"
            assert any(payload["event"] == "node.started"
                       and payload["action"] == "EXECUTE" for _, payload in visible)
            assert not any(payload["event"] == "run.completed" for _, payload in visible)
        finally:
            release.set()
        response = await asyncio.wait_for(task, 3)
        terminal = [payload for _, payload in graph.persistence.events_after(
            request_id, "u_demo_user") if payload["event"] == "run.completed"]
        assert response.status == RunStatus.SUCCEEDED
        assert len(terminal) == 1
        assert terminal[0]["result_ids"] == response.result_ids
        assert terminal[0]["state_version"] == response.state_version

    asyncio.run(scenario())


def test_sse_runtime_uses_only_the_documented_terminal_event():
    graph = build_test_runtime(settings=load_settings().raw)
    response = graph.run(
        message="昨天各品类 GMV 是多少？",
        user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
    )
    names = [event["event"] for event in response.events]
    assert "run.result" not in names
    assert names.count("run.completed") == 1
    terminal = response.events[-1]
    assert terminal["event"] == "run.completed"
    assert terminal["answer"] == response.answer
    assert terminal["result_ids"] == response.result_ids


def test_waiting_thread_resumes_after_runtime_process_restart(tmp_path):
    database = tmp_path / "restart-recovery.db"
    first_store = RuntimePersistence(
        url=f"sqlite:///{database}", create_schema=True)
    first_runtime = build_test_runtime(settings=load_settings().raw)
    first_runtime.persistence = first_store
    waiting = first_runtime.run(
        message="随便看看",
        user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
        request_id="req_before_restart",
    )
    assert waiting.status == RunStatus.WAITING_FOR_USER
    assert waiting.interrupt is not None
    latest = first_store.checkpoint(waiting.thread_id)
    assert latest is not None
    assert waiting.interrupt.checkpoint_id == latest.checkpoint_id
    history_before_restart = first_store.checkpoints_for_thread(waiting.thread_id)
    assert len(history_before_restart) > 1
    assert all(
        child.parent_checkpoint_id == parent.checkpoint_id
        for parent, child in zip(
            history_before_restart, history_before_restart[1:], strict=False)
    )

    # A new graph and a new SQLAlchemy engine model a fresh application process.
    restarted_store = RuntimePersistence(url=f"sqlite:///{database}")
    restarted_runtime = build_test_runtime(settings=load_settings().raw)
    restarted_runtime.persistence = restarted_store
    resumed = restarted_runtime.run(
        message="昨天各品类 GMV",
        user_id="u_demo_user",
        permission=build_test_permission("u_demo_user"),
        thread_id=waiting.thread_id,
        request_id="req_after_restart",
        resume=True,
        expected_state_version=waiting.state_version,
    )
    assert resumed.status == RunStatus.SUCCEEDED
    assert resumed.thread_id == waiting.thread_id
    assert resumed.result_ids
    assert resumed.state_version > waiting.state_version
    persisted = restarted_store.load_state(waiting.thread_id)
    assert persisted is not None and persisted.status == RunStatus.SUCCEEDED
    assert persisted.pending_interrupt is None
