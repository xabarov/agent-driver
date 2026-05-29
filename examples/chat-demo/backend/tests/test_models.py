from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_models_fake_provider_returns_default(client, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    from app.deps import reset_dependency_caches

    reset_dependency_caches()
    response = await client.get("/api/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "fake"
    assert len(payload["models"]) >= 1


@pytest.mark.asyncio
async def test_models_openrouter_proxy(client, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "openrouter")
    monkeypatch.setenv("AGENT_DRIVER_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_DRIVER_BASE_URL", "https://openrouter.ai/api/v1")

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "vendor/model-a", "name": "Model A", "context_length": 128000},
                ]
            }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]):
            assert "models" in url
            assert headers["Authorization"] == "Bearer test-key"
            return _FakeResponse()

    monkeypatch.setattr("app.api.models.httpx.AsyncClient", lambda **kwargs: _FakeClient())
    from app.api import models as models_module
    from app.deps import reset_dependency_caches

    models_module._CACHE["expires_at"] = 0.0
    models_module._CACHE["payload"] = None
    reset_dependency_caches()

    response = await client.get("/api/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openrouter"
    assert payload["models"][0]["id"] == "vendor/model-a"
