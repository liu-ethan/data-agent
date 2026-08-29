from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph
from sqlalchemy.engine import Engine

from backend.app.catalog.models import CatalogSnapshot, MetricSpec, TableRelation
from backend.app.compiler.metric_compiler import compile as compile_metrics
from backend.app.gateway.read_policy import GatewayDecision, check_read_sql
from backend.app.llm.schemas import QuerySkeleton
from backend.app.mysql.execute_read import execute_read
from backend.app.results.store import ResultStore, ResultStoreError
from backend.app.retrieval.schema_rag import retrieve_schema
from backend.app.runtime.permissions import reload_permissions
from backend.app.skills.query.coverage import check_query_coverage
from backend.app.skills.query.followup import (
    decide_followup,
    local_filter_spec,
    merge_query_task,
)
from backend.app.types import (
    Ambiguous,
    CompiledQuery,
    QuerySkillResult,
    QueryTask,
    RuntimeContext,
    SchemaBundle,
    SchemaGap,
    SkillErrorCode,
)

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "query_skeleton.yaml"
_MAX_REPAIRS = 2
_UNREPAIRABLE = (
    "sensitive column",
    "fan-out",
    "join is not in the recalled",
    "bound parameters",
    "expected a single select",
    "forbidden function",
    "locking reads",
)


class QueryLlm(Protocol):
    def query_skeleton(
        self,
        task: QueryTask,
        bundle: SchemaBundle,
        prompt: str,
        *,
        repair_reason: str | None = None,
    ) -> QuerySkeleton: ...


class QueryState(TypedDict, total=False):
    task: QueryTask
    ctx: RuntimeContext
    parent_task: QueryTask | None
    bundle: SchemaBundle | None
    skeleton: QuerySkeleton | None
    compiled: CompiledQuery | None
    repair_count: int
    repair_reason: str | None
    need_repair: bool
    result: QuerySkillResult | None


def _load_prompt() -> str:
    data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("query_skeleton.yaml must be a mapping")
    return str(data["query_skeleton"])


def _relations_from_bundle(bundle: SchemaBundle, catalog: CatalogSnapshot) -> list[TableRelation]:
    index: dict[tuple[str, str, str, str], TableRelation] = {}
    for rel in catalog.relations:
        index[(rel.left_table, rel.right_table, rel.left_col, rel.right_col)] = rel
        index[(rel.right_table, rel.left_table, rel.right_col, rel.left_col)] = rel
    out: list[TableRelation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for join in bundle.joins:
        key = (join["left"], join["right"], join["on_left"], join["on_right"])
        rel = index.get(key)
        if rel is None:
            rel = TableRelation(
                left_table=join["left"],
                right_table=join["right"],
                left_col=join["on_left"],
                right_col=join["on_right"],
                cardinality=join.get("cardinality", "many_to_one"),  # type: ignore[arg-type]
                source="fk",
                version=1,
            )
        ident = (rel.left_table, rel.right_table, rel.left_col, rel.right_col)
        if ident in seen:
            continue
        seen.add(ident)
        out.append(rel)
    return out


def _is_repairable(decision: GatewayDecision) -> bool:
    if decision.ok or decision.kind == "too_broad":
        return False
    reason = (decision.reason or "").lower()
    return not any(token in reason for token in _UNREPAIRABLE)


def _from_decision(decision: GatewayDecision) -> QuerySkillResult:
    code = SkillErrorCode.TOO_BROAD if decision.kind == "too_broad" else SkillErrorCode.UNSAFE_SQL
    return QuerySkillResult(ok=False, error_code=code, error_message=decision.reason)


def _from_store_error(exc: ResultStoreError) -> QuerySkillResult:
    return QuerySkillResult(ok=False, error_code=exc.code, error_message=str(exc))


def _reload(
    ctx: RuntimeContext,
    reload_fn: Callable[..., Any],
    users_db: str | Path | None,
    catalog_version: int,
) -> RuntimeContext:
    permissions = reload_fn(
        ctx.user_id,
        users_db=users_db,
        catalog_version=catalog_version,
    )
    return ctx.model_copy(update={"permissions": permissions, "role": permissions.role})


def build_query_graph(
    *,
    catalog: CatalogSnapshot,
    store: ResultStore,
    llm: QueryLlm,
    retrieve_schema_fn: Callable[..., SchemaBundle | SchemaGap | Ambiguous] | None = None,
    execute_read_fn: Callable[..., str] | None = None,
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    engine: Engine | None = None,
):
    retrieve = retrieve_schema_fn or retrieve_schema
    execute = execute_read_fn or execute_read
    reload_fn = reload_permissions_fn or reload_permissions
    prompt = _load_prompt()

    def followup_node(state: QueryState) -> dict[str, Any]:
        task = state["task"]
        ctx = _reload(state["ctx"], reload_fn, users_db, catalog.catalog_version)
        parent_task = state.get("parent_task")
        if not task.parent_result_id:
            return {"ctx": ctx}
        try:
            parent = store.read_page(task.parent_result_id, ctx)
        except ResultStoreError as exc:
            return {"ctx": ctx, "result": _from_store_error(exc)}
        if parent_task is None:
            return {"ctx": ctx}
        kind = decide_followup(
            task, parent_task=parent_task, parent_columns=parent.columns
        )
        if kind == "requery":
            merged = merge_query_task(parent_task, task)
            return {"ctx": ctx, "task": merged}
        try:
            child_id = store.filter_local(
                task.parent_result_id,
                local_filter_spec(task, parent.columns),
                ctx,
            )
            summary = store.read_page(child_id, ctx)
        except ResultStoreError as exc:
            return {"ctx": ctx, "result": _from_store_error(exc)}
        return {"ctx": ctx, "result": QuerySkillResult(ok=True, result=summary)}

    def q01_coverage(state: QueryState) -> dict[str, Any]:
        ctx = _reload(state["ctx"], reload_fn, users_db, catalog.catalog_version)
        _metrics, err = check_query_coverage(state["task"], ctx, catalog)
        if err is not None:
            return {"ctx": ctx, "result": err}
        return {"ctx": ctx}

    def q02_rag(state: QueryState) -> dict[str, Any]:
        ctx = state["ctx"]
        kwargs: dict[str, Any] = {}
        if hasattr(llm, "table_queries"):
            kwargs["llm"] = llm
        retrieved = retrieve(state["task"], ctx, catalog, **kwargs)
        if isinstance(retrieved, SchemaGap):
            return {
                "result": QuerySkillResult(
                    ok=False,
                    error_code=SkillErrorCode.SCHEMA_GAP,
                    error_message=retrieved.missing_concept,
                    hitl={"schema_gap": retrieved.model_dump()},
                )
            }
        if isinstance(retrieved, Ambiguous):
            return {
                "result": QuerySkillResult(
                    ok=False,
                    error_code=SkillErrorCode.AMBIGUOUS,
                    error_message=retrieved.reason,
                    hitl={"ambiguous": retrieved.model_dump()},
                )
            }
        return {"bundle": retrieved}

    def q08_skeleton(state: QueryState) -> dict[str, Any]:
        bundle = state["bundle"]
        assert bundle is not None
        skeleton = llm.query_skeleton(
            state["task"],
            bundle,
            prompt,
            repair_reason=state.get("repair_reason"),
        )
        return {"skeleton": skeleton, "need_repair": False}

    def q09_compile(state: QueryState) -> dict[str, Any]:
        skeleton = state["skeleton"]
        assert skeleton is not None
        metrics = [
            metric
            for metric in catalog.metrics
            if metric.metric_id in skeleton.metric_ids
        ]
        compiled = compile_metrics(skeleton, metrics, state["task"].time_range)
        return {"compiled": compiled}

    def q10_gateway(state: QueryState) -> dict[str, Any]:
        compiled = state["compiled"]
        bundle = state["bundle"]
        assert compiled is not None and bundle is not None
        joins = _relations_from_bundle(bundle, catalog)
        decision = check_read_sql(
            compiled,
            state["task"],
            catalog,
            joins,
            permissions=state["ctx"].permissions,
        )
        if decision.ok:
            return {"need_repair": False}
        if _is_repairable(decision) and state.get("repair_count", 0) < _MAX_REPAIRS:
            return {
                "need_repair": True,
                "repair_count": state.get("repair_count", 0) + 1,
                "repair_reason": decision.reason,
                "compiled": None,
                "skeleton": None,
            }
        return {"result": _from_decision(decision), "need_repair": False}

    def q11_execute(state: QueryState) -> dict[str, Any]:
        ctx = _reload(state["ctx"], reload_fn, users_db, catalog.catalog_version)
        compiled = state["compiled"]
        bundle = state["bundle"]
        assert compiled is not None and bundle is not None
        joins = _relations_from_bundle(bundle, catalog)
        metrics: list[MetricSpec] = [
            metric
            for metric in catalog.metrics
            if metric.metric_id in state["task"].metric_ids
        ]
        try:
            result_id = execute(
                compiled,
                ctx,
                task=state["task"],
                catalog=catalog,
                store=store,
                allowed_joins=joins,
                engine=engine,
            )
            summary = store.read_page(result_id, ctx)
        except ResultStoreError as exc:
            return {"ctx": ctx, "result": _from_store_error(exc)}
        except Exception as exc:
            code = getattr(exc, "code", SkillErrorCode.REJECTED)
            return {
                "ctx": ctx,
                "result": QuerySkillResult(
                    ok=False,
                    error_code=code if isinstance(code, SkillErrorCode) else SkillErrorCode.REJECTED,
                    error_message=str(exc),
                ),
            }
        units = {metric.metric_id: metric.unit for metric in metrics}
        summary = summary.model_copy(update={"units": units})
        return {"ctx": ctx, "result": QuerySkillResult(ok=True, result=summary)}

    def route_if_done(state: QueryState) -> str:
        return "end" if state.get("result") is not None else "next"

    def route_q10(state: QueryState) -> str:
        if state.get("result") is not None:
            return "end"
        if state.get("need_repair"):
            return "repair"
        return "execute"

    graph = StateGraph(QueryState)
    graph.add_node("followup", followup_node)
    graph.add_node("q01_coverage", q01_coverage)
    graph.add_node("q02_rag", q02_rag)
    graph.add_node("q08_skeleton", q08_skeleton)
    graph.add_node("q09_compile", q09_compile)
    graph.add_node("q10_gateway", q10_gateway)
    graph.add_node("q11_execute", q11_execute)
    graph.add_edge(START, "followup")
    graph.add_conditional_edges(
        "followup",
        route_if_done,
        {"end": END, "next": "q01_coverage"},
    )
    graph.add_conditional_edges(
        "q01_coverage",
        route_if_done,
        {"end": END, "next": "q02_rag"},
    )
    graph.add_conditional_edges(
        "q02_rag",
        route_if_done,
        {"end": END, "next": "q08_skeleton"},
    )
    graph.add_edge("q08_skeleton", "q09_compile")
    graph.add_edge("q09_compile", "q10_gateway")
    graph.add_conditional_edges(
        "q10_gateway",
        route_q10,
        {"end": END, "repair": "q08_skeleton", "execute": "q11_execute"},
    )
    graph.add_edge("q11_execute", END)
    return graph.compile()


def run_query_skill(
    task: QueryTask,
    ctx: RuntimeContext,
    *,
    catalog: CatalogSnapshot,
    store: ResultStore,
    llm: QueryLlm,
    parent_task: QueryTask | None = None,
    retrieve_schema_fn: Callable[..., SchemaBundle | SchemaGap | Ambiguous] | None = None,
    execute_read_fn: Callable[..., str] | None = None,
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    engine: Engine | None = None,
) -> QuerySkillResult:
    graph = build_query_graph(
        catalog=catalog,
        store=store,
        llm=llm,
        retrieve_schema_fn=retrieve_schema_fn,
        execute_read_fn=execute_read_fn,
        reload_permissions_fn=reload_permissions_fn,
        users_db=users_db,
        engine=engine,
    )
    final = graph.invoke(
        {
            "task": task,
            "ctx": ctx,
            "parent_task": parent_task,
            "bundle": None,
            "skeleton": None,
            "compiled": None,
            "repair_count": 0,
            "repair_reason": None,
            "need_repair": False,
            "result": None,
        }
    )
    result = final.get("result")
    if result is None:
        return QuerySkillResult(
            ok=False,
            error_code=SkillErrorCode.REJECTED,
            error_message="query skill produced no result",
        )
    return result
