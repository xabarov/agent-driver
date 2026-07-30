"""Epic 043 D: the empty-final ladder recovers a poisoned (inline-CoT) prefix."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.runtime.single_agent.llm_step.completion import (
    retry_forced_final_without_tools,
)


def _empty(content: str = "") -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=LlmFinishReason.STOP,
        usage=UsageSummary(model_provider="fake", model_name="deepseek-fake"),
        provider="fake",
        model="deepseek-fake",
    )


@pytest.mark.asyncio
async def test_ladder_quarantines_inline_cot_and_recovers() -> None:
    """Provider blanks every call while an assistant turn still exposes <think>;
    once the ladder strips it, the same provider answers normally."""

    def _history_is_poisoned(request: LlmRequest) -> bool:
        return any(
            m.role.value == "assistant" and "<think>" in (m.content or "")
            for m in request.messages
        )

    provider = SimpleNamespace(name="fake", calls=0)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        # Empty as long as the poisoned prefix is present; real answer once cleaned.
        if _history_is_poisoned(request):
            return _empty("")
        return _empty("Here is the real final answer, well over the floor. " * 5)

    provider.complete = complete
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider, fallback_providers=()),
        _emit=lambda spec: None,
    )
    context = SimpleNamespace(
        metadata={"force_final_answer": True},
        run_input=SimpleNamespace(input="do the task", app_metadata={}),
        run_id="run_1",
        attempt_id="attempt_1",
    )
    request = LlmRequest(
        messages=[
            ChatMessage(role="user", content="do the task"),
            ChatMessage(
                role="assistant",
                content="<think>internal plan the classifier will flag</think>",
            ),
            ChatMessage(role="user", content="continue"),
        ],
        model="deepseek-fake",
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
    )

    result = await retry_forced_final_without_tools(
        host, context, request=request, response=_empty("")
    )

    assert context.metadata.get("poisoned_prefix_suspect_turns") == 1
    assert context.metadata.get("poisoned_prefix_quarantine_recovered") is True
    assert "real final answer" in result.message.content
    # The honest give-up signal must NOT have fired — quarantine recovered.
    assert not context.metadata.get("forced_final_empty_after_all_retries")


@pytest.mark.asyncio
async def test_ladder_gives_up_honestly_when_no_cot_to_quarantine() -> None:
    """A clean history that still blanks exhausts to the honest signal — the
    quarantine step must not fire (nothing to sanitize)."""

    provider = SimpleNamespace(name="fake", calls=0)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        return _empty("")

    provider.complete = complete
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider, fallback_providers=()),
        _emit=lambda spec: None,
    )
    context = SimpleNamespace(
        metadata={"force_final_answer": True},
        run_input=SimpleNamespace(input="task", app_metadata={}),
        run_id="run_2",
        attempt_id="attempt_2",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="task")],
        model="deepseek-fake",
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
    )

    await retry_forced_final_without_tools(
        host, context, request=request, response=_empty("")
    )

    assert context.metadata.get("poisoned_prefix_suspect_turns") is None
    assert context.metadata.get("forced_final_empty_after_all_retries") is True
