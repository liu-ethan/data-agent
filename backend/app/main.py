from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.examples import router as examples_router
from app.api.schema import router as schema_router
from app.api.sessions import router as sessions_router
from app.api.tables import router as tables_router
from app.auth.routes import router as auth_router
from app.config import get_settings
from app.db.database import get_connection
from app.db.schema import apply_schema
from app.log.logging import RequestIdMiddleware, log_event


@asynccontextmanager
async def lifespan(_application: FastAPI):
    log_event("INFO", "Waiting for application startup.")
    conn = get_connection()
    try:
        apply_schema(conn)
    finally:
        conn.close()
    log_event("INFO", "Application startup complete.")
    yield
    log_event("INFO", "Application shutdown.")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="data-analysis-agent", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestIdMiddleware)
    application.include_router(schema_router, prefix="/api")
    application.include_router(examples_router, prefix="/api")
    application.include_router(auth_router, prefix="/api")
    application.include_router(chat_router, prefix="/api")
    application.include_router(sessions_router, prefix="/api")
    application.include_router(tables_router, prefix="/api")

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
