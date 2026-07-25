from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.schema import router as schema_router
from app.config import get_settings
from app.log.logging import RequestIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="data-analysis-agent")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestIdMiddleware)
    application.include_router(schema_router, prefix="/api")

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
