"""Optional live provider checks, skipped by default."""

from __future__ import annotations

import os

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.ollama import OllamaProvider
from agent_driver.llm.openai_compatible import OpenAICompatibleProvider
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


load_local_dotenv_for_live_tests()


def _env(name: str, fallback: str | None = None) -> str | None:
    """Resolve env var from AGENT_DRIVER_* or legacy .env names."""
    value = os.getenv(name)
    if value:
        return value
    legacy_map = {
        "AGENT_DRIVER_OPENAI_BASE_URL": "OPENROUTER_BASE_URL",
        "AGENT_DRIVER_OPENAI_API_KEY": "OPENROUTER_API_KEY",
        "AGENT_DRIVER_OPENAI_MODEL": "OPENROUTER_MODEL",
        "AGENT_DRIVER_OLLAMA_BASE_URL": "OLLAMA_BASE_URL",
        "AGENT_DRIVER_OLLAMA_MODEL": "OLLAMA_MODEL",
    }
    legacy = legacy_map.get(name)
    if legacy:
        legacy_value = os.getenv(legacy)
        if legacy_value:
            return legacy_value
    return fallback


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_openai_compatible_healthcheck() -> None:
    """Run optional live OpenAI-compatible healthcheck."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-live",
            base_url=base_url,
            api_key=_env("AGENT_DRIVER_OPENAI_API_KEY"),
            model=model,
        )
    )
    status = await provider.healthcheck()
    assert status.configured is True
    assert status.healthy is True


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_openai_compatible_complete_smoke() -> None:
    """Run optional live OpenRouter/OpenAI-compatible completion smoke test."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-live",
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    )
    response = await provider.complete(
        LlmRequest(
            messages=[
                ChatMessage(role="user", content="Reply with one short greeting.")
            ],
            stream=False,
        )
    )
    assert response.provider == "openai-live"
    assert bool((response.message.content or "").strip())


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_ollama_healthcheck() -> None:
    """Run optional live Ollama healthcheck."""
    base_url = _env("AGENT_DRIVER_OLLAMA_BASE_URL", "http://localhost:11434")
    provider = OllamaProvider(config=OllamaProvider.Config(base_url=base_url))
    status = await provider.healthcheck()
    assert status.configured is True


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_ollama_complete_smoke() -> None:
    """Run optional live Ollama completion smoke test."""
    base_url = _env("AGENT_DRIVER_OLLAMA_BASE_URL", "http://localhost:11434")
    model = _env("AGENT_DRIVER_OLLAMA_MODEL", "llama3:8b")
    provider = OllamaProvider(
        config=OllamaProvider.Config(base_url=base_url, model=model)
    )
    response = await provider.complete(
        LlmRequest(
            messages=[ChatMessage(role="user", content="Say hi in one word.")],
            stream=False,
        )
    )
    assert response.provider == "ollama"
    assert bool((response.message.content or "").strip())
