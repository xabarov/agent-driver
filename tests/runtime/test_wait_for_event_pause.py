"""Epic 045 B: calling wait_for_event parks the run with the subscription."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, RunStatus, ToolCall
from agent_driver.contracts.enums import InterruptReason
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _WaitForEventProvider(FakeProvider):
    """Emits a single wait_for_event call, then (if ever resumed) a final answer."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="m"),
                provider="fake",
                model="m",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="wait_for_event",
                            tool_call_id="c1",
                            args={
                                "event_key": "build.done",
                                "deadline_seconds": 120,
                                "description": "wait for the build to finish",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="the build finished, all green"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
            provider="fake",
            model="m",
            metadata={},
        )


@pytest.mark.asyncio
async def test_wait_for_event_parks_the_run() -> None:
    provider = _WaitForEventProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("wait_for_event"))
    output = await agent.run(
        AgentRunInput(
            input="run the build and tell me when it is done",
            run_id="run_wait_event",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert output.status == RunStatus.PAUSED
    assert output.interrupt is not None
    assert output.interrupt.reason == InterruptReason.WAIT_FOR_EVENT
    subscription = output.interrupt.proposed_action["wait_for_event"]
    assert subscription["event_key"] == "build.done"
    assert subscription["deadline_seconds"] == 120  # bounded, carried through
    # The model has NOT been re-prompted — the loop released instead of polling.
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_wait_for_event_delivers_and_resumes() -> None:
    from agent_driver.contracts.enums import ResumeAction

    provider = _WaitForEventProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("wait_for_event"))
    paused = await agent.run(
        AgentRunInput(
            input="run the build and tell me when it is done",
            run_id="run_wait_deliver",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert paused.status == RunStatus.PAUSED

    # The host's event source fired: deliver the payload as a CLARIFY resume.
    resumed = await agent.resume(
        run_id="run_wait_deliver",
        interrupt_id=paused.interrupt.interrupt_id,
        action=ResumeAction.CLARIFY,
        message="build exited 0; artifacts ready",
    )
    assert resumed.status == RunStatus.COMPLETED
    assert "build finished" in (resumed.answer or "")
    # The run woke and produced a real answer (2nd provider call), no polling in between.
    assert len(provider.requests) == 2
