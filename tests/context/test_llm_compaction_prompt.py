"""Full LLM compaction prompt/path tests."""

from __future__ import annotations

import pytest

from agent_driver.context.compaction import (
    build_full_compaction_prompt,
    run_full_llm_compaction,
    strip_private_draft,
)
from agent_driver.llm.providers_impl.fake import FakeProvider


def test_compaction_prompt_contains_required_sections() -> None:
    """Prompt should require persisted summary schema keys."""
    prompt = build_full_compaction_prompt(
        history_excerpt="history",
        user_request="request",
    )
    assert "<private_draft>" in prompt
    assert "<persisted_summary>" in prompt
    assert "pending_tasks" in prompt


def test_strip_private_draft_removes_private_block() -> None:
    """Private draft should be removed before persisted processing."""
    cleaned, draft = strip_private_draft(
        "<private_draft>secret</private_draft><persisted_summary>{}</persisted_summary>"
    )
    assert draft is not None
    assert "<private_draft>" not in cleaned


@pytest.mark.asyncio
async def test_full_llm_compaction_parses_structured_payload() -> None:
    """Valid fake response should produce successful compaction result."""
    fake_response = (
        "<private_draft>scratchpad</private_draft>"
        "<persisted_summary>{"
        "\"request_intent\":\"intent\","
        "\"key_concepts\":[\"a\"],"
        "\"files_code\":[\"f\"],"
        "\"errors_fixes\":[\"e\"],"
        "\"problems\":[\"p\"],"
        "\"user_messages\":[\"m\"],"
        "\"pending_tasks\":[\"t\"],"
        "\"current_work\":\"work\","
        "\"next_step\":\"next\""
        "}</persisted_summary>"
    )
    provider = FakeProvider(response_text=fake_response)
    result, summary = await run_full_llm_compaction(
        provider=provider,
        model="fake-model",
        history_excerpt="h",
        user_request="u",
    )
    assert result.success is True
    assert "current_work" in summary


@pytest.mark.asyncio
async def test_full_llm_compaction_returns_failure_on_invalid_summary() -> None:
    """Invalid summary payload should return structured failure result."""
    provider = FakeProvider(response_text="<persisted_summary>{\"foo\":\"bar\"}</persisted_summary>")
    result, summary = await run_full_llm_compaction(
        provider=provider,
        model="fake-model",
        history_excerpt="h",
        user_request="u",
    )
    assert result.success is False
    assert summary == {}


def test_compaction_prompt_rolling_merge_mode() -> None:
    """With prior_summary (B2 rolling), the prompt folds the slice into the prior
    summary instead of framing the excerpt as the whole history."""
    plain = build_full_compaction_prompt(history_excerpt="H", user_request="u")
    assert "History excerpt:" in plain
    assert "Prior persisted summary" not in plain

    rolling = build_full_compaction_prompt(
        history_excerpt="NEW_SLICE", user_request="u", prior_summary="PRIOR_SUM"
    )
    assert "Prior persisted summary" in rolling
    assert "PRIOR_SUM" in rolling
    assert "New history slice to fold" in rolling
    assert "NEW_SLICE" in rolling
    assert "History excerpt:" not in rolling


class _PromptCapturingProvider(FakeProvider):
    def __init__(self, response_text: str) -> None:
        super().__init__(response_text=response_text)
        self.last_prompt: str = ""

    async def complete(self, request):  # type: ignore[override]
        msgs = getattr(request, "messages", None) or []
        self.last_prompt = str(getattr(msgs[0], "content", "")) if msgs else ""
        return await super().complete(request)


@pytest.mark.asyncio
async def test_full_llm_compaction_threads_prior_summary() -> None:
    """run_full_llm_compaction forwards prior_summary into the prompt and flags the
    rolling fold in its reduction metadata."""
    fake_response = (
        "<persisted_summary>{"
        "\"request_intent\":\"i\",\"key_concepts\":[\"a\"],\"files_code\":[\"f\"],"
        "\"errors_fixes\":[\"e\"],\"problems\":[\"p\"],\"user_messages\":[\"m\"],"
        "\"pending_tasks\":[\"t\"],\"current_work\":\"w\",\"next_step\":\"n\""
        "}</persisted_summary>"
    )
    provider = _PromptCapturingProvider(response_text=fake_response)
    result, _summary = await run_full_llm_compaction(
        provider=provider,
        model="fake-model",
        history_excerpt="ONLY_NEW",
        user_request="u",
        prior_summary="EARLIER_SUMMARY",
    )
    assert result.success is True
    assert "EARLIER_SUMMARY" in provider.last_prompt
    assert result.metadata.get("rolling_fold") is True
