"""The bounded, real LangGraph runtime for data analysis requests."""

from __future__ import annotations

import asyncio
from functools import partial
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.graph import END, START, StateGraph
import sqlglot
from sqlglot import exp

from ..errors import RuntimeAgentError
from ..models import (Action, AgentState, ChatResponse, Intent, PermissionContext,
                      RunStatus, TaskFrame, TimeRange)
from ..ports import (CatalogRetrievalPort, ReadGatewayPort, RuntimeStateStorePort,
                     StructuredLLMPort)
from ..services.trace import record
from .nodes import (agent_node, execution_gateway_node, query_generation_node,
                    response_node, retrieval_node)
from .state import QueryDraft, TaskUnderstanding, ThreadTitleDraft


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
    """Five top-level LangGraph nodes with deterministic safety routing.

    LLM output is used for open-ended semantic interpretation only.  Coverage,
    permissions, SQL validation and stop conditions remain deterministic.
    """

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
        self.max_iterations = int(runtime.get("max_iterations", 6)); self.max_retrieval_rounds = int(runtime.get("max_retrieval_rounds", 2))
        self.max_query_retries = int(runtime.get("max_query_retries", 1))
        self._compiled = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(_Run)
        graph.add_node("agent_node", partial(agent_node, self))
        graph.add_node("retrieval_node", partial(retrieval_node, self))
        graph.add_node("query_generation_node", partial(query_generation_node, self))
        graph.add_node("execution_gateway_node", partial(execution_gateway_node, self))
        graph.add_node("response_node", partial(response_node, self))
        graph.add_edge(START, "agent_node")
        graph.add_conditional_edges("agent_node", self._route, {"retrieval_node": "retrieval_node", "query_generation_node": "query_generation_node", "execution_gateway_node": "execution_gateway_node", "response_node": "response_node", END: END})
        graph.add_edge("retrieval_node", "agent_node")
        graph.add_edge("query_generation_node", "agent_node")
        graph.add_edge("execution_gateway_node", "agent_node")
        graph.add_edge("response_node", END)
        return graph.compile()

    async def _emit(self, run: _Run, event: str, *, node: str | None = None, action: Action | None = None, **extra: Any) -> None:
        state = run["state"]
        item = {"event": event, "request_id": state.request_id, "thread_id": state.thread_id, "node": node, "action": action.value if action else None, "status": state.status.value, "duration_ms": extra.pop("duration_ms", None), "error_code": extra.pop("error_code", None), "schema_version": "runtime_event_v1", **extra}
        state.action_history.append(item)
        if self.persistence:
            await asyncio.to_thread(self.persistence.append_event, state.request_id, state.user_id, item)
        sink: EventSink | None = run.get("event_sink")
        if sink:
            result = sink(item)
            if result is not None: await result

    async def _checkpoint(self, run: _Run, node: str) -> None:
        if self.persistence:
            state = run["state"]
            checkpoint = await asyncio.to_thread(
                self.persistence.save_checkpoint, state,
                expected_state_version=run.get("checkpoint_version", -1),
                idempotency_key=f"node:{state.request_id}:{node}:{len(state.action_history)}",
                checkpoint_id=(state.pending_interrupt.checkpoint_id
                               if state.status == RunStatus.WAITING_FOR_USER
                               and state.pending_interrupt else None),
            )
            run["checkpoint_version"] = checkpoint.state_version

    @staticmethod
    def _time_range(question: str, timezone_name: str) -> TimeRange:
        # This is intentionally deterministic and the resulting absolute range
        # is checkpointed so resume cannot reinterpret relative time.
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeAgentError("INVALID_TIMEZONE", "timezone must be a valid IANA name") from exc
        anchor = datetime.now(zone)
        today = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        if "昨天" in question: start, end = today - timedelta(days=1), today
        elif "今天" in question or "今日" in question: start, end = today, anchor
        elif "最近 15 天" in question or "最近15天" in question: start, end = anchor - timedelta(days=15), anchor
        elif "本月" in question: start, end = today.replace(day=1), anchor
        else: start, end = today - timedelta(days=1), today
        return TimeRange(start=start, end=end, timezone=timezone_name)

    @staticmethod
    def _canonicalize_parameters(sql: str, parameters: dict[str, Any]) -> str:
        """Normalize common driver placeholders to the gateway's named form."""
        for name in parameters:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise RuntimeAgentError("QUERY_SPEC_MISMATCH", "invalid parameter name")
            sql = sql.replace(f"%({name})s", f":{name}")
        return sql

    @staticmethod
    def _normalize_query_draft(draft: QueryDraft) -> QueryDraft:
        if not draft.candidate_sql:
            return draft
        try:
            tree = sqlglot.parse_one(RuntimeGraph._canonicalize_parameters(draft.candidate_sql, draft.parameters), read="mysql")
        except Exception as exc:
            raise RuntimeAgentError("SQL_PARSE_ERROR", "generated SQL could not be normalized") from exc
        metric_names = []
        for ref in draft.metric_refs:
            lowered = ref.lower()
            metric_names.append(lowered if re.fullmatch(r"[a-z][a-z0-9_]*", lowered) else
                                "gmv" if "gmv" in lowered or "成交额" in ref or "销售额" in ref else
                                "paid_order_count" if "订单" in ref else
                                "refund_amount" if "退款" in ref else re.sub(r"[^a-z0-9_]+", "_", lowered).strip("_") or "metric")
        metric_index = 0; aliases: list[str] = []; expressions = []
        for index, selected in enumerate(tree.selects):
            expression = selected.this if isinstance(selected, exp.Alias) else selected
            if any(isinstance(node, exp.AggFunc) for node in expression.walk()) and metric_index < len(metric_names):
                alias = metric_names[metric_index]; metric_index += 1
            else:
                column = next(expression.find_all(exp.Column), None)
                existing = str(selected.alias or "").lower()
                alias = column.name.lower() if column else existing if re.fullmatch(r"[a-z][a-z0-9_]*", existing) else f"column_{index + 1}"
            aliases.append(alias); expressions.append(expression.as_(alias))
        if expressions and isinstance(tree, exp.Select):
            tree.set("expressions", expressions)
        needs_paid = any(name == "gmv" or name == "paid_order_count" for name in metric_names)
        orders = next((table for table in tree.find_all(exp.Table) if table.name.lower() == "orders"), None)
        has_status = any(column.name.lower() == "status" and column.table in {"", orders.alias_or_name if orders else ""}
                         for column in tree.find_all(exp.Column))
        parameters = dict(draft.parameters)
        if needs_paid and orders and not has_status and isinstance(tree, exp.Select):
            parameters["metric_status"] = "PAID"
            tree = tree.where(exp.column("status", table=orders.alias_or_name).eq(exp.Placeholder(this="metric_status")), append=True)
        return draft.model_copy(update={"candidate_sql": tree.sql(dialect="mysql"), "parameters": parameters,
                                        "expected_columns": aliases})

    async def _understand(self, state: AgentState, message: str, timezone_name: str) -> TaskFrame:
        if self.llm:
            draft, trace = await self.llm.structured(system="You are the task understanding component of a governed ecommerce data analyst. DATA_QUERY means the user asks for actual data values, counts, rankings, comparisons, or a time-bounded result. METRIC_EXPLANATION is only for definition, formula, or methodology questions and never for a request containing a period plus an actual value question such as 'how much'. Requests to read a business field remain DATA_QUERY even when that field may be sensitive; authorization and catalog retrieval decide availability. SCHEMA_QUERY is only about table or field metadata. Greetings, casual conversation, gibberish, capability questions and requests outside governed ecommerce analysis are CHAT_OR_OUT_OF_SCOPE and should use next_action RESPOND. Preserve original user phrases in mentions. Use recent conversation only to resolve references such as '刚才'; do not invent SQL, permissions, catalog IDs, or dates.", user=json.dumps({"question": message, "timezone": timezone_name, "recent_messages": state.messages[-9:-1], "prior_result_ids": state.result_ids[-5:], "prior_artifact_ids": state.artifact_ids[-10:]}, ensure_ascii=False), schema=TaskUnderstanding, purpose="agent", temperature=0.1, prompt_version="task_understanding_v3")
            state.model_traces.append(asdict(trace) | {"purpose": "agent"})
        else:
            # Explicit test double only; create_app always injects StructuredLLM.
            schema = any(term in message.lower() for term in ("字段", "列", "schema", "表结构"))
            metric = "category_gmv" if any(term in message for term in ("品类", "类目")) and any(term in message.lower() for term in ("gmv", "销售", "成交")) else ("gmv" if any(term in message.lower() for term in ("gmv", "销售", "成交")) else "")
            draft = TaskUnderstanding(task_type="SCHEMA_QUERY" if schema else "DATA_QUERY", metric_ids=[metric] if metric else [], dimension_ids=["categories.category_name"] if metric == "category_gmv" else [], mentions={"raw": [message]})
        if state.previous_task_frame and any(term in message for term in ("刚才", "沿用", "继续", "同样")):
            draft.metric_ids = draft.metric_ids or state.previous_task_frame.metric_ids
            draft.dimension_ids = draft.dimension_ids or state.previous_task_frame.dimension_ids
            draft.unresolved = [item for item in draft.unresolved
                                if not any(term in item for term in ("刚才", "沿用", "今天", "今日"))]
        intent = Intent.SCHEMA_QUERY if draft.task_type == "SCHEMA_QUERY" else Intent(draft.task_type)
        return TaskFrame(task_id=f"task_{uuid4().hex[:16]}", user_id=state.user_id, question=message, intent=intent, metric_ids=draft.metric_ids, dimension_ids=draft.dimension_ids, time_range=None if intent in {Intent.SCHEMA_QUERY, Intent.SCHEMA_LOOKUP, Intent.CHAT_OR_OUT_OF_SCOPE} else self._time_range(message, timezone_name), timezone=timezone_name, explicit_conditions=[], deliverables=draft.deliverables, mentions=draft.mentions, unresolved=draft.unresolved)

    @staticmethod
    def _route(run: _Run) -> str:
        state = run["state"]
        if state.status in {RunStatus.WAITING_FOR_USER, RunStatus.FAILED, RunStatus.REJECTED, RunStatus.TIMEOUT} or state.next_action in {Action.ASK_USER, Action.FAIL, Action.END}: return END
        return {Action.RETRIEVE: "retrieval_node", Action.GENERATE: "query_generation_node", Action.EXECUTE: "execution_gateway_node", Action.RESPOND: "response_node"}.get(state.next_action, END)

    async def arun(self, *, message: str, user_id: str, permission: PermissionContext, thread_id: str | None = None, request_id: str | None = None, timezone_name: str = "Asia/Shanghai", event_sink: EventSink | None = None, resume: bool = False, expected_state_version: int | None = None) -> ChatResponse:
        thread_id, request_id = thread_id or f"thread_{uuid4().hex[:16]}", request_id or f"req_{uuid4().hex[:16]}"
        checkpoint = self.persistence.checkpoint(thread_id) if self.persistence else None
        state = self.persistence.load_state(thread_id) if checkpoint and self.persistence else None
        if state and state.user_id != user_id: raise RuntimeAgentError("PERMISSION_DENIED", "thread owner does not match authenticated identity")
        if checkpoint and expected_state_version is None:
            raise RuntimeAgentError("CHECKPOINT_VERSION_REQUIRED", "expected_state_version is required for an existing thread")
        if checkpoint and checkpoint.state_version != expected_state_version:
            raise RuntimeAgentError("CHECKPOINT_CONFLICT", "state version has changed")
        if not state: state = AgentState(thread_id=thread_id, request_id=request_id, user_id=user_id, budgets={"iterations_used": 0, "retrieval_rounds_used": 0, "query_retries_used": 0, "max_iterations": self.max_iterations, "max_retrieval_rounds": self.max_retrieval_rounds})
        else:
            original_question = state.task_frame.question if state.task_frame else ""
            state.previous_task_frame = state.task_frame
            state.request_id, state.status, state.pending_interrupt = request_id, RunStatus.RUNNING, None
            # A clarification answer changes semantic understanding. Rebuild the
            # task and grounded plan while retaining server-side history; reads
            # have no side effects and are safe to recompute.
            state.task_frame = None
            state.grounded_context = None
            state.grounded_context_id = None
            state.coverage = "UNKNOWN"
            state.schema_gap = None
            state.query_plan = None
            state.query_plan_id = None
            state.latest_observation = None
            state.previous_query_error = None
            if resume:
                message = f"{original_question}\n用户澄清：{message}" if original_question else message
            state.budgets = {"iterations_used": 0, "retrieval_rounds_used": 0,
                             "query_retries_used": 0,
                             "max_iterations": self.max_iterations,
                             "max_retrieval_rounds": self.max_retrieval_rounds}
        # Full conversation history belongs to MySQL.  Working State carries a
        # bounded prompt window so checkpoints do not grow with thread age.
        state.messages = [*state.messages[-7:], {"role": "user", "content": message}]
        if self.persistence: await asyncio.to_thread(self.persistence.append_message, thread_id, user_id, "user", message)
        initial: _Run = {"state": state, "message": message, "timezone_name": timezone_name, "permission": permission, "event_sink": event_sink, "checkpoint_version": checkpoint.state_version if checkpoint else -1}
        await self._emit(initial, "run.started")
        terminal_error_code: str | None = None
        model_usage: dict[str, Any] | None = None
        try:
            output = await asyncio.wait_for(self._compiled.ainvoke(initial), timeout=float(self.settings.get("runtime_agent", {}).get("max_total_seconds", 30)))
            final = output["state"]; answer = output.get("final_answer")
            model_usage = output.get("model_usage")
            terminal_error_code = final.previous_query_error
        except asyncio.TimeoutError:
            if self.persistence:
                latest = await asyncio.to_thread(self.persistence.checkpoint, thread_id)
                initial["checkpoint_version"] = latest.state_version if latest else -1
            state.status = RunStatus.TIMEOUT; state.next_action = Action.FAIL; await self._checkpoint(initial, "timeout"); final, answer = state, "请求超时，请缩小范围后重试。"; terminal_error_code = "BUDGET_EXCEEDED"
        except RuntimeAgentError as exc:
            if exc.error_code in {"CHECKPOINT_CONFLICT", "CHECKPOINT_VERSION_REQUIRED"}:
                raise
            if self.persistence:
                latest = await asyncio.to_thread(self.persistence.checkpoint, thread_id)
                initial["checkpoint_version"] = latest.state_version if latest else -1
            state.status = RunStatus.REJECTED if exc.error_code in {"PERMISSION_DENIED", "SQL_FORBIDDEN_OPERATION"} else RunStatus.FAILED; state.next_action = Action.FAIL; await self._checkpoint(initial, "failure"); final, answer = state, exc.message; terminal_error_code = exc.error_code
        except Exception:
            # Provider/driver details stay server-side. Persist a recoverable,
            # public failure instead of allowing FastAPI to emit an HTML 500.
            if self.persistence:
                latest = await asyncio.to_thread(self.persistence.checkpoint, thread_id)
                initial["checkpoint_version"] = latest.state_version if latest else -1
            state.status = RunStatus.FAILED
            state.next_action = Action.FAIL
            await self._checkpoint(initial, "unexpected_failure")
            final, answer = state, "运行失败，请使用 trace_id 排查。"
            terminal_error_code = "INTERNAL_ERROR"
        if final.status == RunStatus.WAITING_FOR_USER: answer = final.pending_interrupt.question if final.pending_interrupt else "需要更多信息。"
        elif final.status in {RunStatus.FAILED, RunStatus.REJECTED, RunStatus.TIMEOUT} and not answer:
            answer = "运行未完成，请根据公开错误码和 trace_id 排查后重试。"
        if self.persistence and final.status == RunStatus.SUCCEEDED: await asyncio.to_thread(self.persistence.append_message, thread_id, user_id, "assistant", answer or "")
        checkpoint = self.persistence.checkpoint(thread_id) if self.persistence else None
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
            await self._emit(terminal_run, "run.completed", **terminal_payload)
            await self._maybe_generate_thread_title(terminal_run, final, answer or "")
        elif final.status in {RunStatus.FAILED, RunStatus.REJECTED, RunStatus.TIMEOUT}:
            await self._emit(terminal_run, "run.failed",
                             error_code=terminal_error_code or "GRAPH_TERMINATED",
                             **terminal_payload)
        return ChatResponse(request_id=request_id, thread_id=thread_id, status=final.status, answer=answer, result_ids=final.result_ids, artifact_ids=final.artifact_ids, events=final.action_history, interrupt=final.pending_interrupt, state_version=checkpoint.state_version if checkpoint else None)

    async def _maybe_generate_thread_title(self, run: _Run, final: AgentState,
                                           answer: str) -> None:
        """Fire-and-forget thread-title summarization for a successful first run."""
        if not self.llm or not self.persistence:
            return
        thread_id = final.thread_id
        user_id = final.user_id
        if self.persistence.load_thread_title(thread_id):
            return
        question = final.task_frame.question if final.task_frame else ""
        if not question:
            return
        asyncio.create_task(self._generate_thread_title(thread_id, user_id,
                                                       question, answer))

    async def _generate_thread_title(self, thread_id: str, user_id: str,
                                    question: str, answer: str) -> None:
        prompt = json.dumps({"question": question, "answer": answer[:400]},
                            ensure_ascii=False)
        try:
            draft, _ = await self.llm.structured(
                system="You are a concise thread-title writer for an ecommerce data analyst. Summarize the user's first question and the assistant's answer into a short Chinese title (no more than 10 Chinese characters, no punctuation, no quotes).",
                user=prompt, schema=ThreadTitleDraft, purpose="thread_title",
                temperature=0.2, prompt_version="thread_title_v1",
            )
            title = draft.title.strip()
            if not title:
                return
            self.persistence.save_thread_title(thread_id, title)
            await asyncio.to_thread(self.persistence.append_event,
                f"title:{thread_id}", user_id, {
                    "event": "thread.title_updated", "request_id": thread_id,
                    "thread_id": thread_id, "node": None, "action": None,
                    "status": "SUCCEEDED", "duration_ms": None,
                    "error_code": None, "thread_title": title,
                    "schema_version": "runtime_event_v1",
                })
        except Exception as exc:  # noqa: BLE001
            record("thread_title.failed", thread_id=thread_id,
                   error_type=type(exc).__name__, error_code=str(exc))

    def run(self, *, message: str, user_id: str, permission: PermissionContext,
            thread_id: str | None = None, request_id: str | None = None,
            timezone_name: str = "Asia/Shanghai",
            event_sink: Callable[[dict[str, Any]], None] | None = None,
            resume: bool = False,
            expected_state_version: int | None = None) -> ChatResponse:
        """Synchronous adapter retained for unit tests and command-line usage."""
        async def sink(item: dict[str, Any]) -> None:
            if event_sink: event_sink(item)
        return asyncio.run(self.arun(message=message, user_id=user_id, permission=permission,
            thread_id=thread_id, request_id=request_id, timezone_name=timezone_name,
            event_sink=sink, resume=resume,
            expected_state_version=expected_state_version))
