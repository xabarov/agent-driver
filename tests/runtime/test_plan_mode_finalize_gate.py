"""Plan mode cannot terminate as ordinary prose before its canonical exit."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ChatMessage, ToolCall
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _response(
    *, content: str = "", tool_name: str | None = None, args: dict | None = None
) -> LlmResponse:
    metadata = {}
    finish_reason = LlmFinishReason.STOP
    if tool_name:
        finish_reason = LlmFinishReason.TOOL_CALLS
        metadata["planned_tool_calls"] = [
            ToolCall(
                tool_name=tool_name,
                tool_call_id=f"call_{tool_name}",
                args=args or {},
            ).model_dump(mode="json")
        ]
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish_reason,
        usage=UsageSummary(model_provider="fake", model_name="test"),
        provider="fake",
        model="test",
        metadata=metadata,
    )


class _PlanThenProseProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []
        self.responses = [
            _response(
                tool_name="enter_plan_mode",
                args={"reason": "substantial work"},
            ),
            _response(content="The plan is ready. Please approve it."),
            _response(
                tool_name="exit_plan_mode_v2",
                args={
                    "reason": "ready",
                    "content": "1. Inspect evidence.\n2. Report.",
                },
            ),
            _response(content="Plan prepared without execution."),
        ]

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]


@pytest.mark.asyncio
async def test_active_plan_mode_rejects_prose_final_and_forces_canonical_exit() -> None:
    provider = _PlanThenProseProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("enter_plan_mode", "exit_plan_mode_v2"),
    )

    output = await agent.run(
        AgentRunInput(
            input="Plan a substantial task",
            run_id="run_plan_mode_finalize_gate",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=4,
        )
    )

    assert output.answer == "Plan prepared without execution."
    assert len(provider.requests) == 4
    assert provider.requests[2].tool_choice == {
        "type": "tool",
        "name": "exit_plan_mode_v2",
    }
    assert "exit_plan_mode_v2 now" in provider.requests[2].messages[-1].content
