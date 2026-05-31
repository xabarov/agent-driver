"""Deterministic fake-provider scenarios for chat-demo development checks."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

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


class DeepResearchSkillsFakeProvider(FakeProvider):
    """Fake provider that exercises skill_view + web_fetch chat-demo UX."""

    def __init__(self) -> None:
        super().__init__(response_text="Deep research skill probe completed.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=16,
            total_tokens=32,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _planned_calls(self) -> list[dict[str, object]]:
        from agent_driver.tools.context import get_workspace_cwd

        workspace = get_workspace_cwd()
        skill_dir = workspace / ".agent-driver" / "skills" / "deep-research-report"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            skill_path.write_text(
                "\n".join(
                    [
                        "---",
                        "name: deep-research-report",
                        "description: Deterministic chat-demo deep research probe.",
                        "allowed-tools: [web_fetch]",
                        "---",
                        "",
                        "# Deep Research Report",
                        "",
                        "Use verified web reads before writing the final answer.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        base_dir = str(skill_dir.parent)
        return [
            ToolCall(
                tool_name="skill_view",
                tool_call_id="fake_skill_view",
                args={
                    "base_dir": base_dir,
                    "name": "deep-research-report",
                    "trusted_roots": [base_dir],
                    "agent_id": "chat-demo-agent",
                },
            ).model_dump(mode="json"),
            ToolCall(
                tool_name="web_fetch",
                tool_call_id="fake_web_fetch",
                args={
                    "url": "https://example.com",
                    "extract_mode": "text",
                    "max_chars": 500,
                },
            ).model_dump(mode="json"),
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
                content=(
                    "Deep research skill probe completed with "
                    "[Example](https://example.com)."
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
                metadata={"planned_tool_calls": self._planned_calls()},
            )
            return
        yield LlmStreamEvent(
            event="delta",
            delta_text="Deep research skill probe completed with ",
        )
        yield LlmStreamEvent(
            event="delta",
            delta_text="[Example](https://example.com).",
        )
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class DeepResearchArtifactFakeProvider(FakeProvider):
    """Fake provider that writes a durable Deep Research report artifact."""

    def __init__(self) -> None:
        super().__init__(response_text="Deep research artifact probe completed.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=24,
            total_tokens=48,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _planned_calls(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="todo_write",
                tool_call_id="fake_deep_todo",
                args={
                    "merge": False,
                    "todos": [
                        {
                            "id": "plan",
                            "content": "Сформировать план исследования",
                            "status": "completed",
                        },
                        {
                            "id": "sources",
                            "content": "Проверить источники",
                            "status": "completed",
                        },
                        {
                            "id": "report",
                            "content": "Записать отчет в artifact",
                            "status": "completed",
                        },
                    ],
                },
            ).model_dump(mode="json"),
            ToolCall(
                tool_name="web_search",
                tool_call_id="fake_deep_search",
                args={
                    "query": "fork-join queueing models computer networks",
                    "mock_results": [
                        {
                            "title": "Fork-join queue",
                            "url": "https://example.com/fork-join",
                            "snippet": "Fork-join queues model split and join workloads.",
                        },
                        {
                            "title": "Queueing networks",
                            "url": "https://example.org/queueing-networks",
                            "snippet": "Queueing networks estimate delay and throughput.",
                        },
                    ],
                },
            ).model_dump(mode="json"),
            ToolCall(
                tool_name="web_fetch",
                tool_call_id="fake_deep_fetch_example_com",
                args={
                    "url": "https://example.com",
                    "extract_mode": "text",
                    "max_chars": 500,
                },
            ).model_dump(mode="json"),
            ToolCall(
                tool_name="web_fetch",
                tool_call_id="fake_deep_fetch_example_org",
                args={
                    "url": "https://example.org",
                    "extract_mode": "text",
                    "max_chars": 500,
                },
            ).model_dump(mode="json"),
            ToolCall(
                tool_name="file_write",
                tool_call_id="fake_deep_report",
                args={
                    "path": "research/report.md",
                    "create_parent": True,
                    "content": "\n".join(
                        [
                            "# Fork-join queueing models",
                            "",
                            "This deterministic Deep Research probe keeps the "
                            "full report in a workspace artifact.",
                            "",
                            "## Findings",
                            "",
                            "- Fork-join models describe jobs split into parallel "
                            "branches and joined after all branches complete.",
                            "- They are useful for estimating latency in systems "
                            "with fan-out/fan-in request paths.",
                            "- Computer network calculations can use the model to "
                            "reason about synchronization delay and throughput.",
                            "",
                            "## Sources",
                            "",
                            "- https://example.com/fork-join",
                            "- https://example.org/queueing-networks",
                            "",
                        ]
                    ),
                },
            ).model_dump(mode="json"),
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
                content=(
                    "Готово: полный отчет сохранен в research/report.md. "
                    "Проверен источник example.com; ключевые выводы: fork-join "
                    "моделирует fan-out/fan-in, задержку синхронизации и "
                    "пропускную способность."
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
                metadata={"planned_tool_calls": self._planned_calls()},
            )
            return
        for chunk in (
            "Готово: полный отчет сохранен в research/report.md. ",
            "Проверен источник example.com; ключевые выводы: fork-join ",
            "моделирует fan-out/fan-in, задержку синхронизации и ",
            "пропускную способность.",
        ):
            yield LlmStreamEvent(event="delta", delta_text=chunk)
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class UntrustedSkillWarningFakeProvider(FakeProvider):
    """Fake provider that loads a skill outside trusted roots."""

    def __init__(self) -> None:
        super().__init__(response_text="Untrusted skill warning probe completed.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=16,
            total_tokens=32,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _planned_calls(self) -> list[dict[str, object]]:
        from agent_driver.tools.context import get_workspace_cwd

        workspace = get_workspace_cwd()
        skill_dir = workspace / "uploaded-skills" / "untrusted-research"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            skill_path.write_text(
                "\n".join(
                    [
                        "---",
                        "name: untrusted-research",
                        "description: Deterministic untrusted skill probe.",
                        "allowed_tools: [python]",
                        "---",
                        "",
                        "# Untrusted Research",
                        "",
                        "This skill intentionally lives outside trusted roots.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        base_dir = str(skill_dir.parent)
        trusted_root = workspace / ".agent-driver" / "trusted-skills"
        trusted_root.mkdir(parents=True, exist_ok=True)
        return [
            ToolCall(
                tool_name="skill_view",
                tool_call_id="fake_untrusted_skill_view",
                args={
                    "base_dir": base_dir,
                    "name": "untrusted-research",
                    "trusted_roots": [str(trusted_root)],
                    "agent_id": "chat-demo-agent",
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
                content="Untrusted skill warning probe completed.",
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
        yield LlmStreamEvent(event="delta", delta_text="Untrusted skill warning ")
        yield LlmStreamEvent(event="delta", delta_text="probe completed.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class CompactionAfterSkillFakeProvider(FakeProvider):
    """Fake provider that makes viewed skill content trigger later compaction."""

    def __init__(self) -> None:
        super().__init__(response_text="Compaction after skill probe completed.")
        self._calls = 0

    def _usage(self, request: LlmRequest) -> UsageSummary:
        return UsageSummary(
            input_tokens=max(
                1, sum(len(message.content) for message in request.messages) // 4
            ),
            output_tokens=16,
            total_tokens=32,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )

    def _planned_calls(self) -> list[dict[str, object]]:
        from agent_driver.tools.context import get_workspace_cwd

        workspace = get_workspace_cwd()
        skill_dir = workspace / ".agent-driver" / "skills" / "large-research-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            skill_path.write_text(
                "\n".join(
                    [
                        "---",
                        "name: large-research-skill",
                        "description: Large deterministic compaction probe skill.",
                        "allowed_tools: [web_search]",
                        "---",
                        "",
                        "# Large Research Skill",
                        "",
                        "Preserve this compact invocation record across compaction.",
                        "",
                        "finding: alpha source https://example.com/alpha",
                        ("detail " * 3200).strip(),
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        base_dir = str(skill_dir.parent)
        return [
            ToolCall(
                tool_name="skill_view",
                tool_call_id="fake_compaction_skill_view",
                args={
                    "base_dir": base_dir,
                    "name": "large-research-skill",
                    "trusted_roots": [base_dir],
                    "agent_id": "chat-demo-agent",
                    "max_chars": 20000,
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
                content="Compaction after skill probe completed.",
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
        yield LlmStreamEvent(event="delta", delta_text="Compaction after skill ")
        yield LlmStreamEvent(event="delta", delta_text="probe completed.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
        )


class ProviderFailureAfterSearchFakeProvider(FakeProvider):
    """Fake provider that searches successfully, then fails the next LLM call."""

    def __init__(self) -> None:
        super().__init__(response_text="Provider failure after search probe.")
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

    def _planned_calls(self) -> list[dict[str, object]]:
        return [
            ToolCall(
                tool_name="web_search",
                tool_call_id="fake_search_before_provider_failure",
                args={
                    "query": "agent-driver provider failure after search probe",
                    "max_results": 1,
                    "mock_results": [
                        {
                            "title": "Provider failure probe",
                            "url": "https://example.com/provider-failure",
                            "snippet": "Deterministic search result before failure.",
                        }
                    ],
                },
            ).model_dump(mode="json")
        ]

    def _raise_provider_error(self) -> None:
        request = httpx.Request("POST", "https://fake.provider.local/chat")
        response = httpx.Response(
            429,
            request=request,
            text="rate limit after web search",
        )
        response.raise_for_status()

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
        self._raise_provider_error()
        raise RuntimeError("unreachable")

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
        self._raise_provider_error()
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.ERROR)


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
    if normalized == "deep_research_skills":
        return DeepResearchSkillsFakeProvider()
    if normalized == "deep_research_artifact":
        return DeepResearchArtifactFakeProvider()
    if normalized == "untrusted_skill_warning":
        return UntrustedSkillWarningFakeProvider()
    if normalized == "compaction_after_skill_invocation":
        return CompactionAfterSkillFakeProvider()
    if normalized == "provider_failure_after_search":
        return ProviderFailureAfterSearchFakeProvider()
    return None
