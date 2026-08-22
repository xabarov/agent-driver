"""Isolated repro for the MeetScript chat_v2 over-iteration («задача уже выполнена» stub).

Verifies, independent of MeetScript, how the single_react loop treats a would-be-final answer.
Finding (2026-07-04): the non-streaming loop is correct for a complete STOP answer (test_A), but
finalizes an EMPTY STOP response without retry (test_empty_*). The 3-call stub over-iteration seen
live is NOT reproduced here → it is streaming-path specific (stream_recovery), a separate repro.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

_LONG_ANSWER = "Вот краткая сводка по каждой встрече корпуса:\n" + "\n".join(
    f"{i}. Встреча {i} — тема {i}." for i in range(1, 40)
)


def _resp(content: str, finish=LlmFinishReason.STOP) -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish,
        usage=UsageSummary(model_provider="fake", model_name="test"),
        provider="fake",
        model="test-model",
    )


class _ScriptProvider(FakeProvider):
    def __init__(self, script) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []
        self._script = script

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return self._script[min(len(self.requests) - 1, len(self._script) - 1)]


async def _run(provider) -> tuple[str, int]:
    agent = create_agent(provider=provider, tools=ToolSet.only("glob_search"))
    output = await agent.run(
        AgentRunInput(
            input="Сделай сводку по каждой встрече",
            run_id="run_stop_no_loop",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile="react_text",
            tool_choice="auto",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    return output.answer or "", len(provider.requests)


@pytest.mark.asyncio
async def test_A_plain_stop_prose_finalizes_in_one_call() -> None:
    """Baseline regression: a complete tool-less STOP answer must finalize in exactly ONE call."""
    answer, calls = await _run(_ScriptProvider([_resp(_LONG_ANSWER)]))
    assert answer == _LONG_ANSWER
    assert calls == 1, f"over-iteration: {calls} calls for a complete STOP answer"


@pytest.mark.asyncio
async def test_empty_stop_is_retried_not_finalized_empty() -> None:
    """An empty STOP first response is retried (bounded) to reach the real answer, not finalized empty.
    Matches the live `final_len=0` occurrences. Fixed by epic 015 Phase C empty-answer retry."""
    answer, calls = await _run(_ScriptProvider([_resp(""), _resp(_LONG_ANSWER)]))
    assert answer == _LONG_ANSWER
    assert calls == 2, f"expected one empty retry then the answer, got {calls} calls"


@pytest.mark.asyncio
async def test_canned_wrong_language_refusal_is_retried() -> None:
    """A canned Chinese «I'm just an AI, haven't learned…» refusal to a Russian question is retried
    (bounded) and reaches the real answer instead of surfacing the refusal (epic 015)."""
    refusal = "作为一个人工智能语言模型，我还没学习如何回答这个问题。"
    answer, calls = await _run(_ScriptProvider([_resp(refusal), _resp(_LONG_ANSWER)]))
    assert answer == _LONG_ANSWER
    assert calls == 2, f"expected one refusal retry then the answer, got {calls} calls"


@pytest.mark.asyncio
async def test_long_repetitive_provider_corruption_is_retried() -> None:
    """A non-empty but pathologically repetitive provider response is not terminal."""
    corrupted = " ".join(["...`"] * 90 + ["(->ai)"] * 5)
    answer, calls = await _run(
        _ScriptProvider([_resp(corrupted), _resp(_LONG_ANSWER)])
    )

    assert answer == _LONG_ANSWER
    assert calls == 2, f"expected one corruption retry then the answer, got {calls} calls"


@pytest.mark.asyncio
async def test_toolcalls_finish_without_parseable_call_does_not_infinite_loop() -> None:
    """finish_reason=TOOL_CALLS but no structured/text-form call: must not spin into a stub."""
    answer, calls = await _run(
        _ScriptProvider(
            [
                _resp(_LONG_ANSWER, finish=LlmFinishReason.TOOL_CALLS),
                _resp("Задача уже выполнена."),
            ]
        )
    )
    assert answer == _LONG_ANSWER, f"got {answer[:60]!r} after {calls} calls"
