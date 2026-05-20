"""FastAPI application factory for chat demo backend."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.providers import router as providers_router
from app.api.sessions import router as sessions_router
from app.api.tools import router as tools_router
from app.deps import get_settings


def create_app() -> FastAPI:
    """Create configured FastAPI application instance."""
    settings = get_settings()
    app = FastAPI(title="agent-driver chat demo backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(tools_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/docs", status_code=307)

    return app

