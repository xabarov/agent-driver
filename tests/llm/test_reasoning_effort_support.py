"""opencode-adoption EPIC-07 — per-model reasoning-effort capability + reject-before-I/O.

Pins the curated capability table (universal tiers always pass; fine tiers validated only
for known models; unknown models permissive) and the provider pre-flight: an unsupported
fine tier raises ``UnsupportedReasoningEffortError`` before any network call.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.llm.reasoning_effort_support import (
    UnsupportedReasoningEffortError,
    effort_from_reasoning_envelope,
    supported_efforts_for_model,
    validate_effort_for_model,
)


# -- capability table -----------------------------------------------------------


@pytest.mark.parametrize("tier", ["none", "low", "medium", "high", None])
@pytest.mark.parametrize(
    "model", ["openai/gpt-4o", "anthropic/claude-3.5", "mystery/unknown-70b"]
)
def test_universal_tiers_never_rejected(tier, model) -> None:
    validate_effort_for_model(tier, model)  # must not raise


def test_anthropic_supports_all_fine_tiers() -> None:
    for tier in ("minimal", "xhigh", "max"):
        validate_effort_for_model(tier, "anthropic/claude-opus-4")


def test_non_reasoning_openai_rejects_fine_tiers() -> None:
    with pytest.raises(UnsupportedReasoningEffortError) as excinfo:
        validate_effort_for_model("xhigh", "openai/gpt-4o")
    assert excinfo.value.tier == "xhigh"
    assert "xhigh" not in excinfo.value.supported


def test_openai_reasoning_model_allows_minimal_rejects_max() -> None:
    validate_effort_for_model("minimal", "openai/o3-mini")  # allowed
    with pytest.raises(UnsupportedReasoningEffortError):
        validate_effort_for_model("max", "openai/o3-mini")


def test_unknown_model_is_permissive_for_fine_tiers() -> None:
    # cannot be confident it will reject -> pass through (avoids false rejects)
    validate_effort_for_model("xhigh", "somevendor/mystery-70b")
    assert supported_efforts_for_model("somevendor/mystery-70b") is None


def test_effort_from_reasoning_envelope() -> None:
    assert effort_from_reasoning_envelope({"effort": "high"}) == "high"
    assert effort_from_reasoning_envelope({"enabled": False}) == "none"
    assert effort_from_reasoning_envelope({}) is None
    assert effort_from_reasoning_envelope(None) is None


# -- provider reject-before-I/O -------------------------------------------------


def _provider(model: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            api_key="test",
            model=model,
            base_url="https://example.invalid/v1",
        )
    )


def _request(effort: str) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        reasoning={"effort": effort},
    )


@pytest.mark.asyncio
async def test_complete_rejects_unsupported_effort_before_io() -> None:
    provider = _provider("openai/gpt-4o")
    # base_url is unreachable; if this raised UnsupportedReasoningEffortError it did so
    # BEFORE any network attempt (the pre-flight runs first).
    with pytest.raises(UnsupportedReasoningEffortError):
        await provider.complete(_request("xhigh"))


@pytest.mark.asyncio
async def test_stream_rejects_unsupported_effort_before_io() -> None:
    provider = _provider("openai/gpt-4o")
    with pytest.raises(UnsupportedReasoningEffortError):
        async for _event in provider.stream(_request("max")):
            pass


def test_preflight_noop_for_portable_tier() -> None:
    provider = _provider("openai/gpt-4o")
    # medium is universal -> pre-flight must not raise (would otherwise proceed to I/O)
    provider._preflight_reasoning(_request("medium"))
