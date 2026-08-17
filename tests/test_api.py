from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.api.app import _interrupt_resumable
from backend.app.models import AgentState, Checkpoint, Interrupt, RunStatus


def test_health_requires_real_dependencies_and_chat_requires_bearer_auth():
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    response = client.post("/api/chat", json={"message": "昨天各品类 GMV 是多少？", "user_id": "u_demo_admin"})
    assert response.status_code == 401 and response.json()["detail"] == "AUTH_REQUIRED"
    allowed = client.options("/api/chat", headers={"Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options("/api/chat", headers={"Origin": "https://not-allowed.example",
        "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in denied.headers


def test_forged_request_identity_never_reaches_runtime():
    client = TestClient(create_app())
    response = client.get("/api/chat/stream", params={"message": "昨天销售额是多少？", "user_id": "u_demo_admin"})
    assert response.status_code == 401 and response.json()["detail"] == "AUTH_REQUIRED"


def test_interrupt_resume_requires_live_matching_persisted_checkpoint():
    now = datetime.now(timezone.utc)
    interrupt = Interrupt(
        reason="SCHEMA_GAP", question="请补充口径", checkpoint_id="ckpt_1",
        interrupt_id="interrupt_1", expires_at=now + timedelta(minutes=5))
    state = AgentState(
        thread_id="thread_1", request_id="req_1", user_id="u_1",
        status=RunStatus.WAITING_FOR_USER, pending_interrupt=interrupt)
    checkpoint = Checkpoint(
        checkpoint_id="ckpt_1", thread_id="thread_1", state_version=3,
        status=RunStatus.WAITING_FOR_USER, serialized_state_ref="state:thread_1:3",
        idempotency_key="node:req_1:agent", created_at=now, updated_at=now)
    assert _interrupt_resumable(
        state, checkpoint, user_id="u_1", interrupt_id="interrupt_1", now=now)
    assert not _interrupt_resumable(
        state, checkpoint.model_copy(update={"checkpoint_id": "ckpt_stale"}),
        user_id="u_1", interrupt_id="interrupt_1", now=now)
    assert not _interrupt_resumable(
        state, checkpoint, user_id="u_1", interrupt_id="interrupt_1",
        now=now + timedelta(minutes=6))


def test_openapi_publishes_the_generated_sse_event_contract():
    application = create_app()
    document = application.openapi()
    event = document["components"]["schemas"]["RuntimeEvent"]
    assert event["properties"]["event"]["enum"] == [
        "run.started", "node.started", "node.completed",
        "interrupt.created", "run.completed", "run.failed",
        "thread.title_updated",
    ]
    for method in ("get", "post"):
        response = document["paths"]["/api/chat/stream"][method]["responses"]["200"]
        assert response["content"]["text/event-stream"]["schema"]["$ref"].endswith(
            "/RuntimeEvent")
    assert "/api/auth/login" in document["paths"]
    assert "/api/auth/demo-token" not in document["paths"]
