"""The bounded, real LangGraph runtime for data analysis requests.

The runtime owns:
* a five-node state machine (`agent_node`, `retrieval_node`,
  `query_generation_node`, `execution_gateway_node`, `response_node`);
* deterministic safety routing and budget enforcement;
* checkpointing, SSE event emission and terminal-state handling.

Everything else (LLM intent resolution, query normalization, time-range
parsing, thread-title summarization) lives in dedicated modules so this
file stays focused on orchestration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from ..errors import RuntimeAgentError
from ..models import (
    Action,
    AgentState,
    ChatResponse,
    PermissionContext,
    RunStatus,
)
from ..ports import (
    CatalogRetrievalPort,
    ReadGatewayPort,
    RuntimeStateStorePort,
    StructuredLLMPort,
)
from ..services.trace import record
from ._events import checkpoint_state, emit_event
from ._thread_title import maybe_generate_thread_title
from .nodes import (
    agent_node,
    execution_gateway_node,
    query_generation_node,
    response_node,
    retrieval_node,
)


class _Run(TypedDict, total=False):
    state: AgentState
    message: str
    timezone_name: str
    permission: PermissionContext
    context: Any
    final_answer: str
    event_sink: Any
    checkpoint_version: int
    model_usage: dict[str, Any]


EventSink = Callable[[dict[str, Any]], Awaitable[None] | None]


class RuntimeGraph:
    """Five top-level LangGraph nodes with deterministic safety routing."""

    def __init__(self, *, retrieval: CatalogRetrievalPort, gateway: ReadGatewayPort,
                 settings: dict[str, Any] | None = None,
                 llm: StructuredLLMPort | None = None,
                 persistence: RuntimeStateStorePort | None = None) -> None:
        self.settings = settings or {}
        self.retrieval = retrieval
        self.gateway = gateway
        self.llm = llm
        self.persistence = persistence
        runtime = self.settings.get("runtime_agent", {})
        self.max_iterations = int(runtime.get("max_iterations", 6))
        self.max_retrieval_rounds = int(runtime.get("max_retrieval_rounds", 2))
        self.max_query_retries = int(runtime.get("max_query_retries", 1))
        self._compiled = self._build_graph()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_graph(self):
        graph = StateGraph(_Run)
        graph.add_node("agent_node", partial(agent_node, self))
        graph.add_node("retrieval_node", partial(retrieval_node, self))
        graph.add_node("query_generation_node", partial(query_generation_node, self))
        graph.add_node("execution_gateway_node", partial(execution_gateway_node, self))
        graph.add_node("response_node", partial(response_node, self))
        graph.add_edge(START, "agent_node")
        graph.add_conditional_edges(
            "agent_node", self._route,
            {
                "retrieval_node": "retrieval_node",
                "query_generation_node": "query_generation_node",
                "execution_gateway_node": "execution_gateway_node",
                "response_node": "response_node",
                END: END,
            },
        )
        graph.add_edge("retrieval_node", "agent_node")
        graph.add_edge("query_generation_node", "agent_node")
        graph.add_edge("execution_gateway_node", "agent_node")
        graph.add_edge("response_node", END)
        return graph.compile()

    @staticmethod
    def _route(run: _Run) -> str:
        state = run["state"]
        if (state.status in {RunStatus.WAITING_FOR_USER, RunStatus.FAILED,
                              RunStatus.REJECTED, RunStatus.TIMEOUT}
                or state.next_action in {Action.ASK_USER, Action.FAIL, Action.END}):
            return END
        return {
            Action.RETRIEVE: "retrieval_node",
            Action.GENERATE: "query_generation_node",
            Action.EXECUTE: "execution_gateway_node",
            Action.RESPOND: "response_node",
        }.get(state.next_action, END)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def arun(self, *, message: str, user_id: str, permission: PermissionContext,
                   thread_id: str | None = None, request_id: str | None = None,
                   timezone_name: str = "Asia/Shanghai",
                   event_sink: EventSink | None = None,
                   resume: bool = False,
                   expected_state_version: int | None = None) -> ChatResponse:
        thread_id, request_id = (thread_id or f"thread_{uuid4().hex[:16]}",
                                 request_id or f"req_{uuid4().hex[:16]}")
        initial = self._open_run(thread_id=thread_id, request_id=request_id,
                                  user_id=user_id, message=message,
                                  timezone_name=timezone_name,
                                  permission=permission, resume=resume,
                                  expected_state_version=expected_state_version)
        initial["event_sink"] = event_sink
        if self.persistence:
            await asyncio.to_thread(
                self.persistence.append_message, thread_id, user_id, "user", message)
        await emit_event(self, initial, "run.started")
        terminal_error_code, model_usage, final, answer = await self._drive(initial)
        return await self._finalize(initial, final=final, answer=answer,
                                    request_id=request_id, thread_id=thread_id,
                                    model_usage=model_usage,
                                    terminal_error_code=terminal_error_code)

    def run(self, *, message: str, user_id: str, permission: PermissionContext,
            thread_id: str | None = None, request_id: str | None = None,
            timezone_name: str = "Asia/Shanghai",
            event_sink: Callable[[dict[str, Any]], None] | None = None,
            resume: bool = False,
            expected_state_version: int | None = None) -> ChatResponse:
        """Synchronous adapter retained for unit tests and command-line usage."""
        async def sink(item: dict[str, Any]) -> None:
            if event_sink:
                event_sink(item)
        return asyncio.run(self.arun(
            message=message, user_id=user_id, permission=permission,
            thread_id=thread_id, request_id=request_id,
            timezone_name=timezone_name, event_sink=sink, resume=resume,
            expected_state_version=expected_state_version))

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _open_run(self, *, thread_id: str, request_id: str, user_id: str,
                  message: str, timezone_name: str, permission: PermissionContext,
                  resume: bool, expected_state_version: int | None) -> _Run:
        checkpoint = self.persistence.checkpoint(thread_id) if self.persistence else None
        state = self.persistence.load_state(thread_id) if checkpoint and self.persistence else None
        if state and state.user_id != user_id:
            raise RuntimeAgentError("PERMISSION_DENIED",
                                    "thread owner does not match authenticated identity")
        if checkpoint and expected_state_version is None:
            raise RuntimeAgentError("CHECKPOINT_VERSION_REQUIRED",
                                    "expected_state_version is required for an existing thread")
        if checkpoint and checkpoint.state_version != expected_state_version:
            raise RuntimeAgentError("CHECKPOINT_CONFLICT",
                                    "state version has changed")
        if state is None:
            state = AgentState(
                thread_id=thread_id, request_id=request_id, user_id=user_id,
                budgets=self._fresh_budgets(),
            )
        else:
            original_question = state.task_frame.question if state.task_frame else ""
            state.previous_task_frame = state.task_frame
            state.request_id = request_id
            state.status = RunStatus.RUNNING
            state.pending_interrupt = None
            # A clarification answer changes semantic understanding. Rebuild
            # the task and grounded plan while retaining server-side history;
            # reads have no side effects and are safe to recompute.
            state.task_frame = None
            state.grounded_context = None
            state.grounded_context_id = None
            state.coverage = "UNKNOWN"
            state.schema_gap = None
            state.query_plan = None
            state.query_plan_id = None
            state.latest_observation = None
            state.previous_query_error = None
            state.last_action_fingerprint = None
            if resume and original_question:
                message = f"{original_question}\n用户澄清：{message}"
            state.budgets = self._fresh_budgets()
        # Working state carries a bounded prompt window; full history lives
        # in MySQL.
        state.messages = [*state.messages[-7:],
                          {"role": "user", "content": message}]
        return {
            "state": state, "message": message, "timezone_name": timezone_name,
            "permission": permission,
            "checkpoint_version": checkpoint.state_version if checkpoint else -1,
        }

    def _fresh_budgets(self) -> dict[str, int]:
        return {
            "iterations_used": 0,
            "retrieval_rounds_used": 0,
            "query_retries_used": 0,
            "max_iterations": self.max_iterations,
            "max_retrieval_rounds": self.max_retrieval_rounds,
        }

    async def _drive(self, initial: _Run):
        state = initial["state"]
        thread_id = state.thread_id
        terminal_error_code: str | None = None
        model_usage: dict[str, Any] | None = None
        try:
            output = await asyncio.wait_for(
                self._compiled.ainvoke(initial),
                timeout=float(self.settings.get("runtime_agent", {}).get(
                    "max_total_seconds", 30)),
            )
            final = output["state"]
            answer = output.get("final_answer")
            model_usage = output.get("model_usage")
            terminal_error_code = final.previous_query_error
        except TimeoutError:
            await self._refresh_checkpoint_version(initial, thread_id)
            state.status = RunStatus.TIMEOUT
            state.next_action = Action.FAIL
            await checkpoint_state(self, initial, "timeout")
            final, answer = state, "请求超时，请缩小范围后重试。"
            terminal_error_code = "BUDGET_EXCEEDED"
        except RuntimeAgentError as exc:
            if exc.error_code in {"CHECKPOINT_CONFLICT", "CHECKPOINT_VERSION_REQUIRED"}:
                raise
            await self._refresh_checkpoint_version(initial, thread_id)
            state.status = (RunStatus.REJECTED
                            if exc.error_code in {"PERMISSION_DENIED",
                                                   "SQL_FORBIDDEN_OPERATION"}
                            else RunStatus.FAILED)
            state.next_action = Action.FAIL
            await checkpoint_state(self, initial, "failure")
            final, answer = state, exc.message
            terminal_error_code = exc.error_code
        except Exception:
            # Provider/driver details stay server-side. Persist a recoverable,
            # public failure instead of allowing FastAPI to emit an HTML 500.
            await self._refresh_checkpoint_version(initial, thread_id)
            state.status = RunStatus.FAILED
            state.next_action = Action.FAIL
            await checkpoint_state(self, initial, "unexpected_failure")
            final, answer = state, "运行失败，请使用 trace_id 排查。"
            terminal_error_code = "INTERNAL_ERROR"
        return terminal_error_code, model_usage, final, answer

    async def _refresh_checkpoint_version(self, initial: _Run, thread_id: str) -> None:
        if not self.persistence:
            return
        latest = await asyncio.to_thread(self.persistence.checkpoint, thread_id)
        initial["checkpoint_version"] = latest.state_version if latest else -1

    async def _finalize(self, initial: _Run, *, final: AgentState, answer: str | None,
                        request_id: str, thread_id: str,
                        model_usage: dict[str, Any] | None,
                        terminal_error_code: str | None) -> ChatResponse:
        if final.status == RunStatus.WAITING_FOR_USER:
            answer = (final.pending_interrupt.question
                      if final.pending_interrupt else "需要更多信息。")
        elif (final.status in {RunStatus.FAILED, RunStatus.REJECTED,
                                  RunStatus.TIMEOUT}
              and not answer):
            answer = "运行未完成，请根据公开错误码和 trace_id 排查后重试。"

        if self.persistence and final.status == RunStatus.SUCCEEDED:
            await asyncio.to_thread(
                self.persistence.append_message, thread_id, final.user_id,
                "assistant", answer or "")
        checkpoint = (self.persistence.checkpoint(thread_id)
                      if self.persistence else None)
        terminal_run: _Run = {**initial, "state": final}
        terminal_payload = {
            "answer": answer,
            "result_ids": list(final.result_ids),
            "artifact_ids": list(final.artifact_ids),
            "interrupt": (final.pending_interrupt.model_dump(mode="json")
                          if final.pending_interrupt else None),
            "state_version": checkpoint.state_version if checkpoint else None,
            "model_usage": model_usage,
        }
        if final.status == RunStatus.SUCCEEDED:
            await emit_event(self, terminal_run, "run.completed", **terminal_payload)
            maybe_generate_thread_title(self, terminal_run, final, answer or "")
        elif final.status in {RunStatus.FAILED, RunStatus.REJECTED,
                                RunStatus.TIMEOUT}:
            await emit_event(self, terminal_run, "run.failed",
                             error_code=terminal_error_code or "GRAPH_TERMINATED",
                             **terminal_payload)
        record("runtime.run_finished", request_id=request_id,
               status=final.status.value, error_code=terminal_error_code)
        return ChatResponse(
            request_id=request_id, thread_id=thread_id, status=final.status,
            answer=answer, result_ids=final.result_ids,
            artifact_ids=final.artifact_ids,
            events=final.action_history,
            interrupt=final.pending_interrupt,
            state_version=checkpoint.state_version if checkpoint else None,
        )
