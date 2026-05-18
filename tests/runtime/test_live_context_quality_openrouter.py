"""Opt-in live context-quality lane against OpenRouter provider."""

from __future__ import annotations

import os

import pytest

from agent_driver.context.compaction import sanitize_compaction_text
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live

load_local_dotenv_for_live_tests()


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


@pytest.mark.asyncio
async def test_live_openrouter_context_quality_lane_returns_parseable_json() -> None:
    """Live lane is opt-in and should return parseable JSON-like response."""
    if not _live_enabled():
        pytest.skip("live tests disabled")
    base_url = os.getenv("AGENT_DRIVER_OPENAI_BASE_URL") or os.getenv(
        "OPENROUTER_BASE_URL"
    )
    model = os.getenv("AGENT_DRIVER_OPENAI_MODEL") or os.getenv("OPENROUTER_MODEL")
    api_key = os.getenv("AGENT_DRIVER_OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not base_url or not model:
        pytest.skip("live OpenRouter env is not configured")
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter-live-context-quality",
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    )
    prompt = sanitize_compaction_text(
        "Return strict JSON with remembered, missing, confidence arrays/fields."
    )
    response = await provider.complete(
        LlmRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            metadata={"lane": "context_quality_live"},
        )
    )
    text = response.message.content.strip()
    assert "remembered" in text
    assert "missing" in text
    assert "confidence" in text
