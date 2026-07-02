from __future__ import annotations

import json

from app.api.providers import _base_url_family, _metadata_dict
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


async def test_providers(client) -> None:
    response = await client.get("/api/providers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "fake"
    assert payload["base_url"] is None
    assert payload["base_url_family"] is None
    assert payload["capability_profile"] is None
    assert payload["route_profile"] is None
    assert payload["provider_preflight"] is None
    assert payload["status"]["provider_name"] == "fake"
    assert payload["status"]["healthy"] is True


def test_provider_status_metadata_exposes_route_preflight_without_secrets() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1?api_key=do-not-leak",
            api_key="sk-test-secret",
            model="openai/gpt-5.5",
        )
    )
    status = provider.status

    route_profile = _metadata_dict(status, "route_profile")
    preflight = _metadata_dict(status, "provider_preflight")
    encoded = json.dumps({"route": route_profile, "preflight": preflight})

    assert _base_url_family(status) == "openrouter"
    assert route_profile is not None
    assert route_profile["base_url_family"] == "openrouter"
    assert preflight is not None
    assert preflight["route_profile_id"] == route_profile["profile_id"]
    assert "sk-test-secret" not in encoded
    assert "do-not-leak" not in encoded
    assert "openrouter.ai" not in encoded
