"""Authenticated FastAPI boundary with real-time SSE forwarding."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..auth import Principal, password_hash_or_dummy, verify_password
from ..bootstrap import RuntimeContainer, build_runtime_container
from ..config import Settings, load_settings
from ..errors import RuntimeAgentError
from ..middleware import TraceMiddleware
from ..models import (
    ArtifactRecord,
    ChatRequest,
    ChatResponse,
    IdentityResponse,
    PasswordLoginRequest,
    PreferenceUpdate,
    RecommendedQuestionsResponse,
    RegisterRequest,
    RegistrationResponse,
    ResultPage,
    ResumeRequest,
    RuntimeEvent,
    ThreadDetail,
    ThreadListResponse,
    TokenResponse,
    TraceContext,
    UserPreferences,
)
from ..services.trace import bind_trace, current_trace

_DEFAULT_RECOMMENDED_QUESTIONS = [
    "昨天各品类的 GMV 是多少？",
    "昨天销售额是多少？",
    "昨天有多少已支付订单？",
    "昨天每个店铺的支付买家数？",
    "上周退款总金额是多少？",
    "orders 表有哪些字段？",
    "昨天哪几个品类的退款最多？",
    "最近 7 天日均 GMV？",
    "各品类订单占比？",
    "products 表有哪些字段？",
]


def _interrupt_resumable(state: Any, checkpoint: Any, *, user_id: str,
                          interrupt_id: str,
                          now: datetime | None = None) -> bool:
    interrupt = state.pending_interrupt if state else None
    return bool(
        checkpoint and state and state.user_id == user_id
        and state.status.value == "WAITING_FOR_USER"
        and interrupt and interrupt.interrupt_id == interrupt_id
        and interrupt.checkpoint_id == checkpoint.checkpoint_id
        and interrupt.expires_at > (now or datetime.now(UTC))
    )


def _fallback_trace(request: Request) -> TraceContext:
    """Synthesize a TraceContext when the middleware is bypassed (e.g. direct unit tests)."""
    return TraceContext(
        trace_id=f"trace_{uuid4().hex}",
        request_id=request.headers.get("X-Request-ID", f"req_{uuid4().hex[:12]}"),
        thread_id="unknown",
        user_id="unknown",
        route=str(request.url.path),
        started_at=datetime.now(UTC),
    )


def create_app(settings: Settings | None = None,
               container: RuntimeContainer | None = None) -> FastAPI:
    settings = settings or load_settings()
    container = container or build_runtime_container(settings)
    app = FastAPI(title=settings.app.name, version="0.2.0")
    cors = settings.server.get("cors", {})
    app.add_middleware(CORSMiddleware, allow_origins=settings.app.cors_origins,
        allow_credentials=bool(cors.get("allow_credentials", False)),
        allow_methods=list(cors.get("allowed_methods", ["GET", "POST", "DELETE", "OPTIONS"])),
        allow_headers=list(cors.get("allowed_headers", ["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"])), max_age=int(cors.get("max_age_seconds", 600)))
    # Registered after CORS so it sits inside the CORS wrapper; per spec 00 §7
    # every request (including /health) carries a trace_id from the middleware.
    app.add_middleware(TraceMiddleware)
    authenticator = container.authenticator
    persistence = container.persistence
    permissions = container.permissions
    gateway = container.gateway
    graph = container.graph
    catalog_repository = container.catalog_repository
    rag_error = container.rag_error
    active_runs: dict[str, asyncio.Task[ChatResponse]] = {}
    app.state.container = container
    app.state.settings, app.state.authenticator, app.state.persistence = settings, authenticator, persistence
    app.state.permissions, app.state.graph, app.state.gateway, app.state.rag_error = permissions, graph, gateway, rag_error
    app.state.active_runs = active_runs

    async def close_llm_client() -> None:
        await container.llm.aclose()
        if container.embedder:
            await container.embedder.aclose()
        if container.catalog_index:
            container.catalog_index.close()

    app.router.add_event_handler("shutdown", close_llm_client)

    @app.exception_handler(RuntimeAgentError)
    async def runtime_error_handler(request: Request, exc: RuntimeAgentError) -> JSONResponse:
        trace = current_trace() or _fallback_trace(request)
        status = 409 if exc.error_code in {"CHECKPOINT_CONFLICT", "CHECKPOINT_VERSION_REQUIRED", "INTERRUPT_INVALID"} else 403 if exc.error_code == "PERMISSION_DENIED" else 400
        return JSONResponse(status_code=status, content=exc.as_model(trace.trace_id).model_dump(mode="json"))

    async def principal(request: Request) -> Principal:
        return await authenticator.principal(request)

    def permission_for(identity: Principal):
        return permissions.for_principal(identity)

    def timezone_for(identity: Principal, requested: str | None) -> str:
        if requested:
            return requested
        preferences = persistence.user_preferences(identity.user_id)
        return str(preferences.get("timezone") or settings.app.timezone)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # Spec 00 §8: /health reports service, database and version. The RAG
        # (Milvus) subsystem is reported under ``capabilities`` but does NOT
        # flip the top-level ``status`` to ``degraded``: spec 00 §3 allows
        # Milvus to remain off until spec 04 lands.
        try:
            persistence_ok, mysql_ok = await asyncio.to_thread(persistence.healthcheck), await asyncio.to_thread(gateway.data.healthcheck)
        except Exception:
            persistence_ok, mysql_ok = False, False
        return {
            "status": "ok" if persistence_ok and mysql_ok else "degraded",
            "service": settings.app.name,
            "version": app.version,
            "environment": settings.app.environment,
            "database": {"configured": True, "connected": mysql_ok, "persistence_connected": persistence_ok},
            "rag": {"configured": rag_error is None, "error_code": rag_error},
            "capabilities": {"rag": rag_error is None},
            "schema_version": "health_v3",
        }

    @app.post("/api/auth/login", response_model=TokenResponse)
    async def login(body: PasswordLoginRequest) -> TokenResponse:
        identity = await asyncio.to_thread(persistence.login_identity, body.account)
        valid = await asyncio.to_thread(
            verify_password, body.password,
            password_hash_or_dummy(
                str(identity.get("password_hash"))
                if identity and identity.get("password_hash") else None),
        )
        if not identity or not identity.get("active") or not valid:
            # Do not reveal whether an account exists, is disabled, or has no password.
            raise HTTPException(status_code=401, detail="AUTH_INVALID_CREDENTIALS")
        token = authenticator.issue(
            str(identity["user_id"]), [str(identity["role_name"])])
        return TokenResponse(
            access_token=token,
            expires_in=authenticator.expire_minutes * 60,
        )

    @app.post("/api/auth/register", response_model=RegistrationResponse,
              status_code=201)
    async def register(body: RegisterRequest) -> RegistrationResponse:
        invite = await asyncio.to_thread(
            persistence.consume_invite_code, body.invite_code, body.role)
        if not invite:
            raise HTTPException(status_code=400, detail="INVITE_INVALID")
        try:
            await asyncio.to_thread(persistence.register_user,
                account=body.account, password=body.password,
                role=body.role, policy_version=str(invite["policy_version"]))
        except RuntimeAgentError as exc:
            if exc.error_code == "ACCOUNT_TAKEN":
                raise HTTPException(status_code=409, detail="ACCOUNT_TAKEN") from exc
            raise
        return RegistrationResponse(account=body.account, role=body.role)

    @app.get("/api/recommended_questions", response_model=RecommendedQuestionsResponse)
    async def recommended_questions() -> RecommendedQuestionsResponse:
        items = list(settings.raw.get("runtime_agent", {}).get(
            "recommended_questions") or _DEFAULT_RECOMMENDED_QUESTIONS)
        return RecommendedQuestionsResponse(items=items[:10])

    @app.get("/api/me", response_model=IdentityResponse)
    async def me(identity: Principal = Depends(principal)) -> IdentityResponse:
        context = permission_for(identity)
        return IdentityResponse(user_id=identity.user_id, roles=context.roles,
            policy_version=context.policy_version, expires_at=context.expires_at)

    @app.get("/api/settings", response_model=UserPreferences)
    async def get_settings(identity: Principal = Depends(principal)) -> UserPreferences:
        return UserPreferences(values=await asyncio.to_thread(
            persistence.user_preferences, identity.user_id))

    @app.put("/api/settings", response_model=UserPreferences)
    async def update_settings(body: PreferenceUpdate,
                              identity: Principal = Depends(principal)) -> UserPreferences:
        allowed = set(settings.raw.get("memory", {}).get("long_term", {}).get(
            "allowed_keys", ["timezone", "default_shop_id", "chart_preference", "number_format"]))
        if body.key not in allowed:
            raise HTTPException(status_code=400, detail="MEMORY_KEY_NOT_ALLOWED")
        try:
            await asyncio.to_thread(persistence.put_user_preference, identity.user_id,
                                    body.key, body.value, confirmed=body.confirmed)
        except RuntimeAgentError as exc:
            status = 400 if exc.error_code == "MEMORY_CONFIRMATION_REQUIRED" else 500
            raise HTTPException(status_code=status, detail=exc.error_code) from exc
        return UserPreferences(values=await asyncio.to_thread(
            persistence.user_preferences, identity.user_id))

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, identity: Principal = Depends(principal)) -> ChatResponse:
        if body.user_id and body.user_id != identity.user_id:
            raise HTTPException(status_code=403, detail="IDENTITY_MISMATCH")
        context = permission_for(identity)
        request_id = body.request_id or request.headers.get("X-Request-ID") or f"req_{uuid4().hex[:16]}"
        bind_trace(thread_id=body.thread_id or "pending", user_id=identity.user_id)
        key = f"request-result:{identity.user_id}:{request_id}"
        cached = await asyncio.to_thread(persistence.get_idempotent, key)
        if cached: return ChatResponse.model_validate(cached)
        response = await graph.arun(message=body.message, user_id=identity.user_id, permission=context, thread_id=body.thread_id, request_id=request_id, timezone_name=timezone_for(identity, body.timezone), expected_state_version=body.expected_state_version)
        trace = current_trace()
        if trace:
            response.trace_id = trace.trace_id
        stored = await asyncio.to_thread(persistence.put_idempotent, key, response.model_dump(mode="json"))
        return ChatResponse.model_validate(stored)

    _stream_docs = {200: {
        "model": RuntimeEvent,
        "content": {"text/event-stream": {
            "schema": {"$ref": "#/components/schemas/RuntimeEvent"}}},
    }}

    async def _chat_stream(*, identity: Principal, request: Request,
                           message: str | None, thread_id: str | None,
                           request_id: str | None,
                           expected_state_version: int | None,
                           timezone_name: str | None,
                           start_run: bool) -> StreamingResponse:
        if start_run and not (message or "").strip():
            raise HTTPException(status_code=400, detail="MESSAGE_REQUIRED")
        if not start_run and not (request_id or request.headers.get("X-Request-ID")):
            raise HTTPException(status_code=400, detail="STREAM_REQUEST_ID_REQUIRED")
        context = permission_for(identity)
        if start_run and thread_id:
            checkpoint = await asyncio.to_thread(persistence.checkpoint, thread_id)
            state = await asyncio.to_thread(persistence.load_state, thread_id)
            if not checkpoint or not state:
                raise HTTPException(status_code=404, detail="THREAD_NOT_FOUND")
            if state.user_id != identity.user_id:
                raise HTTPException(status_code=403, detail="PERMISSION_DENIED")
            if expected_state_version is None:
                raise HTTPException(status_code=409, detail="CHECKPOINT_VERSION_REQUIRED")
            if checkpoint.state_version != expected_state_version:
                raise HTTPException(status_code=409, detail="CHECKPOINT_CONFLICT")
        request_id = request_id or request.headers.get("X-Request-ID") or f"req_{uuid4().hex[:16]}"
        run_thread_id = thread_id or f"thread_{uuid4().hex[:16]}"
        result_key = f"request-result:{identity.user_id}:{request_id}"
        run_key = f"{identity.user_id}:{request_id}"

        async def worker() -> ChatResponse:
            bind_trace(thread_id=run_thread_id, user_id=identity.user_id)
            trace = current_trace()
            try:
                response = await graph.arun(
                    message=message or "", user_id=identity.user_id,
                    permission=context, thread_id=run_thread_id, request_id=request_id,
                    timezone_name=timezone_for(identity, timezone_name),
                    expected_state_version=expected_state_version)
                if trace:
                    response.trace_id = trace.trace_id
            except Exception as exc:
                error_code = (exc.error_code if isinstance(exc, RuntimeAgentError)
                              else "INTERNAL_ERROR")
                response = ChatResponse(
                    request_id=request_id, thread_id=run_thread_id, status="FAILED",
                    answer="运行失败，请使用 trace_id 排查。",
                    trace_id=trace.trace_id if trace else None)
                await asyncio.to_thread(
                    persistence.append_event, request_id, identity.user_id,
                    RuntimeEvent(
                        event="run.failed", request_id=request_id,
                        thread_id=run_thread_id, status="FAILED",
                        error_code=error_code, answer=response.answer,
                    ).model_dump(mode="json"),
                )
            stored = await asyncio.to_thread(
                persistence.put_idempotent, result_key, response.model_dump(mode="json"))
            return ChatResponse.model_validate(stored)

        if (start_run
                and not await asyncio.to_thread(persistence.get_idempotent, result_key)
                and run_key not in active_runs):
            task = asyncio.create_task(worker(), name=f"runtime:{run_key}")
            active_runs[run_key] = task
            task.add_done_callback(lambda _: active_runs.pop(run_key, None))
        try:
            after_id = max(0, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            after_id = 0

        async def stream():
            cursor = after_id
            while True:
                rows = await asyncio.to_thread(
                    persistence.events_after, request_id, identity.user_id, cursor, 100)
                for event_id, payload in rows:
                    cursor = event_id
                    yield (
                        f"id: {event_id}\nevent: {payload['event']}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                cached = await asyncio.to_thread(persistence.get_idempotent, result_key)
                running = run_key in active_runs
                if cached and not rows:
                    return
                if not start_run and not running and not cached and not rows:
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat/stream", responses=_stream_docs)
    async def chat_stream_post(
            body: ChatRequest, request: Request,
            identity: Principal = Depends(principal)) -> StreamingResponse:
        return await _chat_stream(
            identity=identity, request=request, message=body.message,
            thread_id=body.thread_id,
            request_id=body.request_id or request.headers.get("X-Request-ID"),
            expected_state_version=body.expected_state_version,
            timezone_name=body.timezone, start_run=True)

    @app.get("/api/chat/stream", responses=_stream_docs)
    async def chat_stream_get(
            request: Request, message: str | None = None,
            thread_id: str | None = None, request_id: str | None = None,
            expected_state_version: int | None = None,
            timezone_name: str | None = Query(None, alias="timezone"),
            identity: Principal = Depends(principal)) -> StreamingResponse:
        start_run = bool((message or "").strip())
        return await _chat_stream(
            identity=identity, request=request, message=message,
            thread_id=thread_id, request_id=request_id,
            expected_state_version=expected_state_version,
            timezone_name=timezone_name, start_run=start_run)

    @app.get("/api/results/{result_id}", response_model=ResultPage)
    async def result(result_id: str, offset: int = 0, limit: int = 100, identity: Principal = Depends(principal)) -> ResultPage:
        try: return ResultPage.model_validate(persistence.page_result(result_id, identity.user_id, max(0, offset), min(max(limit, 1), 1000)))
        except RuntimeAgentError as exc: raise HTTPException(status_code=403, detail=exc.error_code) from exc
        except KeyError as exc: raise HTTPException(status_code=404, detail="RESULT_NOT_FOUND") from exc

    @app.get("/api/results/{result_id}/export.csv")
    async def export_result(result_id: str, identity: Principal = Depends(principal)) -> Response:
        try: content = await asyncio.to_thread(persistence.csv_result, result_id, identity.user_id)
        except RuntimeAgentError as exc: raise HTTPException(status_code=403, detail=exc.error_code) from exc
        except KeyError as exc: raise HTTPException(status_code=404, detail="RESULT_NOT_FOUND") from exc
        return Response(content=content, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{result_id}.csv"'})

    @app.get("/api/threads", response_model=ThreadListResponse)
    async def threads(limit: int = 50, identity: Principal = Depends(principal)) -> ThreadListResponse:
        items = await asyncio.to_thread(persistence.list_threads, identity.user_id, min(max(limit, 1), 100))
        return ThreadListResponse.model_validate({"items": items})

    @app.get("/api/threads/{thread_id}", response_model=ThreadDetail)
    async def thread(thread_id: str, identity: Principal = Depends(principal)) -> ThreadDetail:
        try: return ThreadDetail.model_validate(await asyncio.to_thread(persistence.thread_detail, thread_id, identity.user_id))
        except RuntimeAgentError as exc: raise HTTPException(status_code=403, detail=exc.error_code) from exc
        except KeyError as exc: raise HTTPException(status_code=404, detail="THREAD_NOT_FOUND") from exc

    @app.delete("/api/threads/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str, identity: Principal = Depends(principal)) -> Response:
        try:
            await asyncio.to_thread(persistence.delete_thread, thread_id, identity.user_id)
        except RuntimeAgentError as exc:
            status = 403 if exc.error_code == "PERMISSION_DENIED" else 500
            raise HTTPException(status_code=status, detail=exc.error_code) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="THREAD_NOT_FOUND") from exc
        return Response(status_code=204)

    @app.post("/api/threads/{thread_id}/interrupts/{interrupt_id}/resume", response_model=ChatResponse)
    async def resume(thread_id: str, interrupt_id: str, body: ResumeRequest, request: Request, identity: Principal = Depends(principal)) -> ChatResponse:
        if body.user_id and body.user_id != identity.user_id: raise HTTPException(status_code=403, detail="IDENTITY_MISMATCH")
        idempotency_key = f"resume-result:{identity.user_id}:{body.client_request_id}"
        cached = await asyncio.to_thread(persistence.get_idempotent, idempotency_key)
        if cached: return ChatResponse.model_validate(cached)
        checkpoint = persistence.checkpoint(thread_id); state = persistence.load_state(thread_id)
        if not _interrupt_resumable(
                state, checkpoint, user_id=identity.user_id,
                interrupt_id=interrupt_id):
            raise HTTPException(status_code=409, detail="INTERRUPT_INVALID")
        if checkpoint.state_version != body.expected_state_version: raise HTTPException(status_code=409, detail="CHECKPOINT_CONFLICT")
        context = permission_for(identity)
        response = await graph.arun(message=body.answer, user_id=identity.user_id,
            permission=context, thread_id=thread_id,
            request_id=f"resume_{body.client_request_id}",
            timezone_name=state.task_frame.timezone if state.task_frame else "Asia/Shanghai",
            resume=True, expected_state_version=body.expected_state_version)
        stored = await asyncio.to_thread(persistence.put_idempotent, idempotency_key,
                                         response.model_dump(mode="json"))
        return ChatResponse.model_validate(stored)

    @app.get("/api/artifacts/{artifact_id}", response_model=ArtifactRecord)
    async def artifact(artifact_id: str, identity: Principal = Depends(principal)) -> ArtifactRecord:
        try:
            permission = permission_for(identity)
            catalog_version = await asyncio.to_thread(
                catalog_repository.version, permission.allowed_source_ids)
            return ArtifactRecord.model_validate(persistence.get_artifact_record(
                artifact_id, user_id=identity.user_id, permission=permission,
                catalog_version=catalog_version))
        except RuntimeAgentError as exc: raise HTTPException(status_code=410, detail=exc.error_code) from exc

    return app


app = create_app()
