"""Epic 041 C: a stalled compaction provider fails gracefully, never hangs."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_driver.context.compaction.llm_full import run_full_llm_compaction
from agent_driver.contracts import CompactionMode
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, LlmStreamEvent

_VALID_SUMMARY = (
    "<persisted_summary>"
    '{"request_intent":"x","key_concepts":[],"files_code":[],"errors_fixes":[],'
    '"problems":[],"user_messages":[],"pending_tasks":[],"current_work":"w",'
    '"next_step":"n"}'
    "</persisted_summary>"
)


class _StallingSummaryProvider:
    name = "fake"

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(role="assistant", content=_VALID_SUMMARY),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
            provider="fake",
            model="m",
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(event="delta", delta_text="<persisted")
        await asyncio.sleep(3600)  # stall mid-summary


@pytest.mark.asyncio
async def test_stalled_compaction_returns_graceful_failure() -> None:
    result, summary = await run_full_llm_compaction(
        provider=_StallingSummaryProvider(),
        model="m",
        history_excerpt="a\nb\nc",
        user_request="q",
        idle_timeout_seconds=0.05,
    )
    assert result.success is False
    assert result.mode == CompactionMode.LLM_FULL
    assert result.metadata.get("failure_kind") == "aux_idle_timeout"
    assert summary == {}


@pytest.mark.asyncio
async def test_compaction_without_timeout_uses_complete_and_succeeds() -> None:
    # No idle timeout → plain complete (never touches the stalling stream) → success.
    result, summary = await run_full_llm_compaction(
        provider=_StallingSummaryProvider(),
        model="m",
        history_excerpt="a\nb\nc",
        user_request="q",
    )
    assert result.success is True
    assert summary["current_work"] == "w"
