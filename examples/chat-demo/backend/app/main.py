"""FastAPI application factory for chat demo backend."""

from __future__ import annotations

from pathlib import Path

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.providers import router as providers_router
from app.api.sessions import router as sessions_router
from app.api.skills import router as skills_router
from app.api.tools import router as tools_router
from app.api.workspace import router as workspace_router
from app.deps import get_settings
from app.observability import setup_tracing
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Create configured FastAPI application instance."""
    settings = get_settings()
    setup_tracing(settings)
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
    app.include_router(models_router, prefix="/api")
    app.include_router(skills_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")

    if _STATIC_DIR.exists():
        app.mount(
            "/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets"
        )

        @app.get("/", include_in_schema=False)
        def spa_index() -> FileResponse:
            return FileResponse(_STATIC_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> FileResponse:
            first_segment = full_path.split("/", 1)[0] if full_path else ""
            if first_segment in {"api", "docs", "redoc", "openapi.json"}:
                return RedirectResponse(url="/docs", status_code=307)
            candidate = _STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_STATIC_DIR / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        def root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/docs", status_code=307)

    return app
