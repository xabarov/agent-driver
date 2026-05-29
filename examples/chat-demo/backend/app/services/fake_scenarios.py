"""Deterministic fake-provider scenarios for chat-demo development checks."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_driver.contracts import ChatMessage
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider


class PlanApprovalFakeProvider(FakeProvider):
    """Fake provider that exercises the plan approval interrupt path."""

    def __init__(self) -> None:
        super().__init__(response_text="Plan approved. Ready to execute.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=8,
            total_tokens=16,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _planned_calls(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="exit_plan_mode_v2",
                tool_call_id="plan_approval_demo",
                args={
                    "reason": "ready for review",
                    "plan_id": "plan_chat_demo",
                    "path": "docs/chat-demo-plan.md",
                    "content": (
                        "1. Inspect the requested change.\n"
                        "2. Apply the smallest safe implementation.\n"
                        "3. Run focused tests and a chat-demo smoke check."
                    ),
                },
            ).model_dump(mode="json")
        ]

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        usage = self._usage(request)
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider=self.name,
                model=request.model or "fake-model",
                metadata={
                    "provider_kind": "fake",
                    "planned_tool_calls": self._planned_calls(),
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content="Plan approved. Ready to execute.",
            ),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider=self.name,
            model=request.model or "fake-model",
            metadata={"provider_kind": "fake"},
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self._calls += 1
        usage = self._usage(request)
        if self._calls == 1:
            yield LlmStreamEvent(
                event="tool_calls",
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                metadata={"planned_tool_calls": self._planned_calls()},
            )
            return
        yield LlmStreamEvent(event="delta", delta_text="Plan approved. ")
        yield LlmStreamEvent(event="delta", delta_text="Ready to execute.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


def build_fake_scenario_provider(scenario: str | None) -> FakeProvider | None:
    """Return a scenario provider, or None when no scenario is selected."""
    if (scenario or "").strip() == "plan_approval":
        return PlanApprovalFakeProvider()
    return None
