"""Runtime tests for multi-step CodeAgent loop."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import FakeRestrictedCodeExecutor
from agent_driver.contracts import AgentProfile, AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


class SequencedFakeProvider(FakeProvider):
    """Fake provider that returns predefined response sequence."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(response_text=responses[-1] if responses else "done")
        self._responses = list(responses)
        self._cursor = 0

    async def complete(self, request):  # type: ignore[override]
        _ = request
        idx = self._cursor
        if self._cursor < len(self._responses) - 1:
            self._cursor += 1
        response_text = self._responses[idx]
        return LlmResponse(
            message=ChatMessage(role="assistant", content=response_text),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(total_tokens=10),
            provider=self.name,
            model="fake-model",
            metadata={"provider_kind": "fake"},
        )


@pytest.mark.asyncio
async def test_code_agent_loops_until_final_answer() -> None:
    """CodeAgent should loop back to llm_call until final_answer appears."""
    provider = SequencedFakeProvider(
        [
            "```python\nprint('step1')\n```",
            "```python\nfinal_answer(7)\n```",
        ]
    )
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="calculate",
            run_id="run_code_loop",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            max_steps=12,
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["tool_results"][-1]["summary"] == "7"
    llm_call_started = [e for e in output.events if e.type.value == "llm_call_started"]
    assert len(llm_call_started) >= 2


@pytest.mark.asyncio
async def test_code_agent_loop_respects_max_steps_limit() -> None:
    """CodeAgent should fail deterministically on max_steps in endless loop."""
    provider = SequencedFakeProvider(["```python\nprint('again')\n```"])
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="loop",
            run_id="run_code_loop_limit",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            max_steps=4,
        )
    )
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "max_steps_exceeded"
