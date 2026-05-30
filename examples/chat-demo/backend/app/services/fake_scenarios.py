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


class ForcePlanningBlockFakeProvider(FakeProvider):
    """Fake provider that first attempts a gated write before planning."""

    def __init__(self) -> None:
        super().__init__(response_text="Force planning blocked the file write.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=12,
            total_tokens=24,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _write_call(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="file_write",
                tool_call_id="force_planning_block_demo",
                args={
                    "path": "docs/force-planning-demo.txt",
                    "content": (
                        "This write should be blocked until a plan is approved.\n"
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
                    "planned_tool_calls": self._write_call(),
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content=(
                    "Force planning blocked the file write. I need to enter "
                    "plan mode and get approval before retrying."
                ),
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
                metadata={"planned_tool_calls": self._write_call()},
            )
            return
        yield LlmStreamEvent(event="delta", delta_text="Force planning blocked ")
        yield LlmStreamEvent(event="delta", delta_text="the file write.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class PythonPolicyRecoveryFakeProvider(FakeProvider):
    """Fake provider that recovers from a blocked python import."""

    def __init__(self) -> None:
        super().__init__(response_text="Python policy recovery completed.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=14,
            total_tokens=28,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _blocked_python_call(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="python",
                tool_call_id="python_policy_blocked_demo",
                args={
                    "code": "import os\nprint(os.getcwd())",
                    "session_id": "policy_recovery_demo",
                },
            ).model_dump(mode="json")
        ]

    def _safe_python_call(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="python",
                tool_call_id="python_policy_safe_demo",
                args={
                    "code": "import math\nprint(math.comb(5, 2))",
                    "session_id": "policy_recovery_demo",
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
                    "planned_tool_calls": self._blocked_python_call(),
                },
            )
        if self._calls == 2:
            return LlmResponse(
                message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider=self.name,
                model=request.model or "fake-model",
                metadata={
                    "provider_kind": "fake",
                    "planned_tool_calls": self._safe_python_call(),
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content=(
                    "Импорт os заблокирован политикой sandbox, поэтому я "
                    "переписал расчет на разрешенный math. "
                    "Результат math.comb(5, 2) = 10."
                ),
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
                metadata={"planned_tool_calls": self._blocked_python_call()},
            )
            return
        if self._calls == 2:
            yield LlmStreamEvent(
                event="tool_calls",
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                metadata={"planned_tool_calls": self._safe_python_call()},
            )
            return
        yield LlmStreamEvent(
            event="delta",
            delta_text="Импорт os заблокирован политикой sandbox, ",
        )
        yield LlmStreamEvent(
            event="delta",
            delta_text=(
                "поэтому я переписал расчет на разрешенный math. "
                "Результат math.comb(5, 2) = 10."
            ),
        )
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class CompactionNoticeFakeProvider(FakeProvider):
    """Fake provider for a deterministic compaction-notice live probe."""

    def __init__(self) -> None:
        super().__init__(response_text="Context compaction probe completed.")

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

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content="Context compaction probe completed.",
            ),
            finish_reason=LlmFinishReason.STOP,
            usage=self._usage(request),
            provider=self.name,
            model=request.model or "fake-model",
            metadata={"provider_kind": "fake"},
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(
            event="delta",
            delta_text="Context compaction probe ",
        )
        yield LlmStreamEvent(event="delta", delta_text="completed.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=self._usage(request),
        )


def build_fake_scenario_provider(scenario: str | None) -> FakeProvider | None:
    """Return a scenario provider, or None when no scenario is selected."""
    normalized = (scenario or "").strip()
    if normalized == "plan_approval":
        return PlanApprovalFakeProvider()
    if normalized == "force_planning_block":
        return ForcePlanningBlockFakeProvider()
    if normalized == "python_policy_recovery":
        return PythonPolicyRecoveryFakeProvider()
    if normalized == "compaction_notice":
        return CompactionNoticeFakeProvider()
    return None
