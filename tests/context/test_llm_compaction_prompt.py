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
