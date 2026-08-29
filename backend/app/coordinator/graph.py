from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RunnableConfig

from backend.app.catalog.models import CatalogSnapshot
from backend.app.coordinator.candidates import enrich_hitl, lookup_metrics, lookup_products, lookup_time_range
from backend.app.coordinator.hitl import interrupt_hitl as interrupt
from backend.app.coordinator.progress import emit_think
from backend.app.coordinator.intent import (
    IntentDraft,
    build_query_task,
    build_write_task,
    load_coordinator_prompt,
    normalize_intent,
)
from backend.app.coordinator.respond import (
    build_response_prompt,
    empty_result_answer,
    facts_from_summary,
    ground_answer,
)
from backend.app.resources.domain import empty_thread_title, tenant_id as default_tenant_id
from backend.app.resources.sql import load_sql
from backend.app.results.store import ResultStore, ResultStoreError
from backend.app.types import (
    Intent,
    PermissionSet,
    QuerySkillResult,
    QueryTask,
    ResultSummary,
    RuntimeContext,
    SkillErrorCode,
    WriteSkillResult,
    WriteTask,
)

ReloadFn = Callable[..., PermissionSet]
QueryFn = Callable[..., QuerySkillResult]
PrepareFn = Callable[..., WriteSkillResult]
ExecuteFn = Callable[..., WriteSkillResult]
LookupFn = Callable[..., list[dict[str, str]]]


class CoordinatorState(TypedDict, total=False):
    message: str
    intent: str | None
    query_task: QueryTask | None
    parent_query_task: QueryTask | None
    write_task: WriteTask | None
    result_id: str | None
    operation_id: str | None
    request_hash: str | None
    preview: dict[str, Any] | None
    hitl: dict[str, Any] | None
    hitl_resume: Any
    answer: str | None
    error_code: str | None
    error_message: str | None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def upsert_thread(
    runtime_db: str | Path,
    thread_id: str,
    user_id: str,
    title: str,
    now: str,
) -> None:
    with sqlite3.connect(runtime_db) as conn:
        conn.execute(
            load_sql("threads.upsert_thread"),
            (thread_id, user_id, title or empty_thread_title(), now, now),
        )
        conn.commit()


def sqlite_checkpointer(path: str | Path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def invoke_coordinator(graph, message: str, ctx: RuntimeContext, *, resume: Any = None) -> dict:
    config = {
        "configurable": {
            "thread_id": ctx.thread_id,
            "user_id": ctx.user_id,
            "request_time_utc": ctx.request_time_utc,
            "timezone": ctx.timezone,
            "role": ctx.role,
            "tenant_id": ctx.tenant_id,
        }
    }
    if resume is not None:
        return graph.invoke(Command(resume=resume), config)
    return graph.invoke({"message": message}, config)


def _sku_ids(summary: ResultSummary) -> list[str] | None:
    if "sku_id" in summary.columns:
        key = "sku_id"
    elif "id" in summary.columns:
        key = "id"
    else:
        return None
    ids: list[str] = []
    for row in summary.preview_rows:
        value = row.get(key)
        if value is not None:
            ids.append(str(value))
    return ids


def build_coordinator_graph(
    *,
    llm: Any,
    catalog: CatalogSnapshot,
    store: ResultStore,
    run_query_fn: QueryFn | None = None,
    prepare_write_fn: PrepareFn | None = None,
    execute_write_fn: ExecuteFn | None = None,
    lookup_products_fn: LookupFn | None = None,
    lookup_metrics_fn: LookupFn | None = None,
    lookup_time_fn: LookupFn | None = None,
    reload_permissions_fn: ReloadFn | None = None,
    runtime_db: str | Path | None = None,
    checkpointer: Any | None = None,
):
    prompt = load_coordinator_prompt()
    products_fn = lookup_products_fn
    metrics_fn = lookup_metrics_fn
    time_fn = lookup_time_fn
    reload_fn = reload_permissions_fn
    query_fn = run_query_fn
    prepare_fn = prepare_write_fn
    execute_fn = execute_write_fn

    def make_ctx(config: RunnableConfig) -> RuntimeContext:
        conf = config["configurable"]
        user_id = conf["user_id"]
        if reload_fn is None:
            from backend.app.runtime.permissions import reload_permissions

            perms = reload_permissions(user_id, catalog_version=catalog.catalog_version)
        else:
            perms = reload_fn(user_id)
        return RuntimeContext(
            tenant_id=conf.get("tenant_id", default_tenant_id()),
            user_id=user_id,
            role=perms.role,
            request_time_utc=conf["request_time_utc"],
            timezone=conf["timezone"],
            permissions=perms,
            thread_id=conf["thread_id"],
        )

    def start_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        ctx = make_ctx(config)
        if runtime_db is not None:
            upsert_thread(runtime_db, ctx.thread_id, ctx.user_id, empty_thread_title(), _now_iso())
        parent = state.get("parent_query_task")
        prev = state.get("query_task")
        if prev is not None and state.get("result_id"):
            parent = prev
        return {
            "parent_query_task": parent,
            "intent": None,
            "hitl": None,
            "hitl_resume": None,
            "answer": None,
            "error_code": None,
            "error_message": None,
            "write_task": None,
            "preview": None,
            "operation_id": None,
            "request_hash": None,
        }

    def plan_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        emit_think("plan")
        ctx = make_ctx(config)
        parent = state.get("parent_query_task")
        draft: IntentDraft = llm.classify_intent(
            state.get("message") or "",
            prompt,
            has_parent_query=parent is not None,
        )
        intent = normalize_intent(draft)
        updates: dict[str, Any] = {"intent": intent.value}
        if intent == Intent.UNSUPPORTED:
            updates["answer"] = "该请求不受支持。请先问数，再单独提交写入。"
            return updates
        if intent == Intent.CLARIFY:
            updates["hitl"] = _clarify_hitl(
                draft,
                ctx,
                catalog,
                products_fn=products_fn,
                metrics_fn=metrics_fn,
                time_fn=time_fn,
                user_message=state.get("message") or "",
                llm=llm,
            )
            return updates
        if intent in (Intent.QUERY, Intent.FOLLOWUP):
            updates["query_task"] = build_query_task(
                draft,
                ctx,
                parent=parent,
                result_id=state.get("result_id") if intent == Intent.FOLLOWUP else None,
            )
            return updates
        object_ids, hitl = _resolve_write_objects(draft, ctx, store, state.get("result_id"))
        if hitl is not None:
            updates["hitl"] = enrich_hitl(
                hitl,
                catalog=catalog,
                permissions=ctx.permissions,
                llm=llm,
                user_message=state.get("message") or "",
            )
            return updates
        updates["write_task"] = build_write_task(draft, ctx, object_ids)
        return updates

    def run_query_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        emit_think("run_query")
        ctx = make_ctx(config)
        task = state["query_task"]
        assert query_fn is not None
        result = query_fn(task, ctx, parent_task=state.get("parent_query_task"))
        user_message = state.get("message") or ""
        if result.ok and result.result is not None:
            return {
                "result_id": result.result.result_id,
                "error_code": None,
                "error_message": None,
                "hitl": None,
            }
        if result.error_message == "metric_ids required":
            return {
                "error_code": None,
                "error_message": None,
                "hitl": _clarify_hitl(
                    IntentDraft(intent=Intent.CLARIFY, clarify_kind="metric"),
                    ctx,
                    catalog,
                    products_fn=products_fn,
                    metrics_fn=metrics_fn,
                    time_fn=time_fn,
                    user_message=user_message,
                    llm=llm,
                ),
            }
        code = result.error_code
        if code in (
            SkillErrorCode.SCHEMA_GAP,
            SkillErrorCode.AMBIGUOUS,
            SkillErrorCode.TOO_BROAD,
            SkillErrorCode.UNSAFE_SQL,
        ):
            hitl = {
                "kind": "query_error",
                "error_code": None if code is None else code.value,
                "error_message": result.error_message,
                **(result.hitl or {}),
            }
            return {
                "error_code": None if code is None else code.value,
                "error_message": result.error_message,
                "hitl": enrich_hitl(
                    hitl,
                    catalog=catalog,
                    permissions=ctx.permissions,
                    llm=llm,
                    user_message=user_message,
                ),
            }
        return {
            "error_code": None if code is None else code.value,
            "error_message": result.error_message,
            "hitl": None,
        }

    def prepare_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        emit_think("prepare_write")
        ctx = make_ctx(config)
        task = state["write_task"]
        assert prepare_fn is not None and task is not None
        result = prepare_fn(task, ctx)
        if not result.ok:
            return {
                "error_code": None if result.error_code is None else result.error_code.value,
                "error_message": result.error_message,
                "hitl": None,
            }
        preview = dict(result.preview or {})
        operation_id = result.operation_id or preview.get("operation_id")
        request_hash = preview.get("request_hash")
        preview.setdefault("operation_id", operation_id)
        preview.setdefault("request_hash", request_hash)
        return {
            "operation_id": operation_id,
            "request_hash": request_hash,
            "preview": preview,
            "hitl": {"kind": "write_preview", **preview},
            "error_code": None,
            "error_message": None,
        }

    def hitl_node(state: CoordinatorState) -> dict[str, Any]:
        emit_think("hitl")
        return {"hitl_resume": interrupt(state.get("hitl"))}

    def execute_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        emit_think("execute_write")
        ctx = make_ctx(config)
        resume = state.get("hitl_resume") or {}
        if resume.get("user_id") and resume.get("user_id") != ctx.user_id:
            return {
                "error_code": SkillErrorCode.REJECTED.value,
                "error_message": "approver must be the same operator",
                "hitl": None,
            }
        assert execute_fn is not None
        result = execute_fn(
            state.get("operation_id"),
            state.get("request_hash"),
            ctx,
            preview=state.get("preview") or {},
        )
        if result.error_code == SkillErrorCode.VERSION_CONFLICT and result.preview:
            preview = dict(result.preview)
            return {
                "operation_id": result.operation_id or preview.get("operation_id"),
                "request_hash": preview.get("request_hash"),
                "preview": preview,
                "error_code": SkillErrorCode.VERSION_CONFLICT.value,
                "hitl": {"kind": "write_preview", **preview},
            }
        return {
            "operation_id": result.operation_id or state.get("operation_id"),
            "error_code": None if result.error_code is None else result.error_code.value,
            "error_message": result.error_message,
            "hitl": None,
        }

    def respond_node(state: CoordinatorState, config: RunnableConfig) -> dict[str, Any]:
        emit_think("respond")
        if state.get("answer"):
            return {}
        ctx = make_ctx(config)
        resume = state.get("hitl_resume") or {}
        if (state.get("hitl") or {}).get("kind") == "write_preview" and resume.get("approved") is False:
            return {"answer": "已取消写入。", "hitl": None}
        if state.get("intent") == Intent.UNSUPPORTED.value:
            return {"answer": "该请求不受支持。请先问数，再单独提交写入。"}
        if state.get("operation_id") and resume.get("approved") and not state.get("error_code"):
            facts = {
                "operation_id": state.get("operation_id"),
                "result_id": state.get("result_id"),
            }
            prompt_text = build_response_prompt(facts)
            answer = llm.compose_answer(prompt_text, facts)
            return {"answer": ground_answer(answer, facts)}
        result_id = state.get("result_id")
        if result_id and not state.get("error_code"):
            summary = store.read_page(result_id, ctx)
            empty = empty_result_answer(summary)
            if empty:
                return {"answer": empty}
            facts = facts_from_summary(summary)
            prompt_text = build_response_prompt(facts)
            raw = llm.compose_answer(prompt_text, facts)
            return {"answer": ground_answer(raw, facts)}
        return {"answer": state.get("error_message") or "无法完成该请求。"}

    def route_plan(state: CoordinatorState) -> str:
        intent = state.get("intent")
        if intent == Intent.UNSUPPORTED.value:
            return "respond"
        if state.get("hitl"):
            return "hitl"
        if intent == Intent.WRITE.value:
            return "prepare"
        if intent in (Intent.QUERY.value, Intent.FOLLOWUP.value):
            return "query"
        return "respond"

    def route_query(state: CoordinatorState) -> str:
        if state.get("hitl"):
            return "hitl"
        return "respond"

    def route_hitl(state: CoordinatorState) -> str:
        resume = state.get("hitl_resume") or {}
        kind = (state.get("hitl") or {}).get("kind")
        if kind == "write_preview" and resume.get("approved"):
            return "execute"
        if kind == "query_error":
            return "query"
        return "respond"

    def route_execute(state: CoordinatorState) -> str:
        if (state.get("hitl") or {}).get("kind") == "write_preview":
            return "hitl"
        return "respond"

    graph = StateGraph(CoordinatorState)
    graph.add_node("start", start_node)
    graph.add_node("plan", plan_node)
    graph.add_node("run_query", run_query_node)
    graph.add_node("prepare_write", prepare_node)
    graph.add_node("hitl", hitl_node)
    graph.add_node("execute_write", execute_node)
    graph.add_node("respond", respond_node)
    graph.add_edge(START, "start")
    graph.add_edge("start", "plan")
    graph.add_conditional_edges(
        "plan",
        route_plan,
        {"respond": "respond", "hitl": "hitl", "prepare": "prepare_write", "query": "run_query"},
    )
    def route_prepare(state: CoordinatorState) -> str:
        return "hitl" if state.get("hitl") else "respond"

    graph.add_conditional_edges(
        "run_query",
        route_query,
        {"hitl": "hitl", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "prepare_write",
        route_prepare,
        {"hitl": "hitl", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "hitl",
        route_hitl,
        {"execute": "execute_write", "query": "run_query", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "execute_write",
        route_execute,
        {"hitl": "hitl", "respond": "respond"},
    )
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def _clarify_hitl(
    draft: IntentDraft,
    ctx: RuntimeContext,
    catalog: CatalogSnapshot,
    *,
    products_fn: LookupFn | None,
    metrics_fn: LookupFn | None,
    time_fn: LookupFn | None,
    user_message: str = "",
    llm: Any | None = None,
) -> dict[str, Any]:
    query = draft.clarify_query or user_message
    if draft.clarify_kind == "metric":
        fn = metrics_fn or (lambda q, perms, **k: lookup_metrics(q, catalog, perms))
        candidates = fn(query, ctx.permissions)
    elif draft.clarify_kind == "time":
        fn = time_fn or (lambda q, perms, **k: lookup_time_range(perms))
        candidates = fn(query, ctx.permissions)
    else:
        fn = products_fn or (lambda q, perms, **k: lookup_products(q, perms))
        candidates = fn(query, ctx.permissions)
    payload: dict[str, Any] = {
        "kind": "clarify",
        "clarify_kind": draft.clarify_kind,
        "query": query,
        "candidates": candidates,
    }
    if not candidates:
        payload["status"] = "not_found"
        payload["message"] = "未查到"
    return enrich_hitl(
        payload,
        catalog=catalog,
        permissions=ctx.permissions,
        llm=llm,
        user_message=user_message,
    )


def _resolve_write_objects(
    draft: IntentDraft,
    ctx: RuntimeContext,
    store: ResultStore,
    result_id: str | None,
) -> tuple[list[str], dict[str, Any] | None]:
    if not draft.refer_previous_skus:
        return list(draft.object_ids), None
    if not result_id:
        return [], {
            "kind": "clarify",
            "status": "not_found",
            "message": "未查到",
            "candidates": [],
            "error_code": SkillErrorCode.RESULT_EXPIRED.value,
        }
    try:
        summary = store.read_page(result_id, ctx, limit=100)
    except ResultStoreError as exc:
        return [], {
            "kind": "query_error",
            "status": "not_found",
            "message": str(exc),
            "candidates": [],
            "error_code": exc.code.value,
        }
    ids = _sku_ids(summary)
    if not ids:
        return [], {
            "kind": "clarify",
            "status": "not_found",
            "message": "未查到",
            "candidates": [],
            "error_code": SkillErrorCode.RESULT_EXPIRED.value,
        }
    return ids, None
