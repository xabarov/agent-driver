"""Shared test fixtures for chat demo backend."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.deps import reset_dependency_caches
from app.main import create_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Build FastAPI app with isolated environment configuration."""
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "safe")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    application = create_app()
    yield application
    reset_dependency_caches()


@pytest_asyncio.fixture()
async def client(app) -> AsyncIterator[AsyncClient]:
    """Async HTTP client over in-process ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

