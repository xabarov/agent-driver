"""HITL pause/resume integration tests for governed runtime."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ChatMessage,
    ResumeAction,
    RuntimeEventType,
    ToolCall,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    SqliteRuntimeStore,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools import register_planning_tool
from agent_driver.runtime.errors import MissingCheckpointError, RuntimeExecutionError
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


class _PlanApprovalThenWriteProvider(FakeProvider):
    """Provider that requests plan approval, then a write, then stops."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        usage = UsageSummary(model_provider="fake", model_name="test")
        if self.calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider="fake",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="exit_plan_mode_v2",
                            tool_call_id="plan_call",
                            args={
                                "plan_id": "plan_force_1",
                                "content": "1. Inspect\n2. Write\n3. Verify",
                                "requested_tools": ["file_write"],
                                "target_urls": ["file:///workspace"],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self.calls == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider="fake",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            tool_call_id="write_call",
                            args={"path": "x.txt", "content": "ok"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="done"),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model="test",
        )


class _PlanRefinementProvider(FakeProvider):
    """Provider that first restates, then correctly revises a pending plan."""

    def __init__(self, *, always_prose: bool = False) -> None:
        super().__init__(response_text="done")
        self.calls = 0
        self.always_prose = always_prose
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        self.calls += 1
        usage = UsageSummary(model_provider="fake", model_name="test")
        if self.calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider="fake",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="exit_plan_mode_v2",
                            tool_call_id="initial_plan_call",
                            args={
                                "plan_id": "plan_initial",
                                "content": "1. Active check\n2. Verify",
                                "requested_tools": ["web_request"],
                                "target_urls": ["https://lab.example/"],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self.always_prose or self.calls == 2:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content="The plan is ready. Please approve it.",
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=usage,
                provider="fake",
                model="test",
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=usage,
            provider="fake",
            model="test",
            metadata={
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="exit_plan_mode_v2",
                        tool_call_id="revised_plan_call",
                        args={
                            "plan_id": "plan_revised",
                            "content": "1. Passive check\n2. Review before active checks",
                            "requested_tools": ["web_request"],
                            "target_urls": ["https://lab.example/"],
                        },
                    ).model_dump(mode="json")
                ]
            },
        )


class _MisbehavingPlanRefinementProvider(FakeProvider):
    """Attempts an ordinary tool after an invalid revised-plan call."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.calls = 0
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        self.calls += 1
        usage = UsageSummary(model_provider="fake", model_name="test")
        if self.calls == 1:
            tool_call = ToolCall(
                tool_name="exit_plan_mode_v2",
                tool_call_id="initial_plan_call",
                args={
                    "plan_id": "plan_initial",
                    "content": "1. Active check\n2. Verify",
                    "requested_tools": ["danger"],
                    "target_urls": ["https://lab.example/"],
                },
            )
        elif self.calls == 2:
            tool_call = ToolCall(
                tool_name="exit_plan_mode_v2",
                tool_call_id="invalid_revised_plan_call",
                args={
                    "content": "Try to bypass refinement",
                    "requested_tools": ["continue_without_plan"],
                    "target_urls": [],
                },
            )
        elif self.calls == 3:
            tool_call = ToolCall(
                tool_name="danger",
                tool_call_id="unapproved_danger_call",
                args={"target": "https://lab.example/"},
            )
        elif self.calls == 4:
            tool_call = ToolCall(
                tool_name="exit_plan_mode_v2",
                tool_call_id="unchanged_revised_plan_call",
                args={
                    "plan_id": "plan_initial",
                    "content": "1. Active check\n2. Verify",
                    "requested_tools": ["danger"],
                    "target_urls": ["https://lab.example/"],
                },
            )
        else:
            tool_call = ToolCall(
                tool_name="exit_plan_mode_v2",
                tool_call_id="revised_plan_call",
                args={
                    "plan_id": "plan_revised",
                    "content": "1. Passive check\n2. Review before active checks",
                    "requested_tools": ["danger"],
                    "target_urls": ["https://lab.example/"],
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=usage,
            provider="fake",
            model="test",
            metadata={"planned_tool_calls": [tool_call.model_dump(mode="json")]},
        )


@pytest.mark.asyncio
async def test_runner_interrupts_for_high_risk_policy() -> None:
    """Runner should return paused output when policy requests interrupt."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    governed = GovernedToolExecutor(registry=registry)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=wrap_governed_executor(governed)),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_interrupt_1",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert any(event.type.value == "interrupt_requested" for event in output.events)


@pytest.mark.asyncio
async def test_runner_resume_approve_executes_pending_tool_once() -> None:
    """Approve resume should execute pending call exactly once."""
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_approve",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    resume_output = await runner.run(
        AgentRunInput(
            run_id="run_hitl_approve",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resume_output.status.value == "completed"
    assert len(calls) == 1
    assert calls[0]["target"] == "x"


@pytest.mark.asyncio
async def test_runner_pauses_for_exit_plan_mode_approval_and_resumes() -> None:
    """Plan exit should pause for approval, then resume through existing HITL path."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    plan_call = ToolCall(
        tool_name="exit_plan_mode_v2",
        args={
            "content": "1. Inspect\n2. Implement\n3. Verify",
            "requested_tools": ["file_write"],
            "target_urls": ["file:///workspace"],
        },
        tool_call_id="plan_call",
    )
    paused = await runner.run(
        AgentRunInput(
            input="approve plan",
            run_id="run_plan_approval_hitl",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                metadata={"planned_tool_calls": [plan_call.model_dump(mode="json")]},
            ),
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    assert paused.interrupt.reason.value == "plan_approval_required"
    approval = paused.metadata["approval_payload"]
    assert approval["tool_name"] == "exit_plan_mode_v2"
    assert paused.interrupt.proposed_action["plan_approval"]["content_hash"]
    paused_event_types = [event.type for event in paused.events]
    assert RuntimeEventType.PLAN_ARTIFACT_UPDATED in paused_event_types
    assert RuntimeEventType.PLAN_APPROVAL_REQUESTED in paused_event_types

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_plan_approval_hitl",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    assert any(event.type == RuntimeEventType.PLAN_APPROVED for event in resumed.events)


@pytest.mark.asyncio
async def test_runner_plan_approval_survives_sqlite_store_reload(tmp_path) -> None:
    """Plan approval interrupts should resume after durable store reload."""
    path = tmp_path / "runtime.sqlite3"
    registry = ToolRegistry()
    register_planning_tool(registry)
    config = RunnerConfig(
        tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
    )
    first_store = SqliteRuntimeStore(path=str(path))
    first_runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=first_store,
        event_log=first_store,
        config=config,
    )
    plan_call = ToolCall(
        tool_name="exit_plan_mode_v2",
        args={
            "content": "1. Inspect\n2. Implement\n3. Verify",
            "requested_tools": ["file_write"],
            "target_urls": ["file:///workspace"],
        },
        tool_call_id="plan_call",
    )

    paused = await first_runner.run(
        AgentRunInput(
            input="approve durable plan",
            run_id="run_plan_approval_sqlite_reload",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                metadata={"planned_tool_calls": [plan_call.model_dump(mode="json")]},
            ),
        )
    )

    assert paused.status.value == "paused"
    assert paused.interrupt is not None

    second_store = SqliteRuntimeStore(path=str(path))
    second_runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=second_store,
        event_log=second_store,
        config=config,
    )
    resumed = await second_runner.run(
        AgentRunInput(
            run_id="run_plan_approval_sqlite_reload",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert resumed.status.value == "completed"
    event_types = [event.type for event in second_store.list_for_run(resumed.run_id)]
    assert RuntimeEventType.PLAN_APPROVAL_REQUESTED in event_types
    assert RuntimeEventType.PLAN_APPROVED in event_types


@pytest.mark.asyncio
async def test_runner_plan_approval_marks_force_planning_approved() -> None:
    """Approved plan should unblock force-planning gated write tools."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    writes: list[dict[str, object]] = []

    async def _file_write(args):
        writes.append(dict(args))
        return {"summary": "wrote"}

    registry.register(
        danger_tool_manifest().model_copy(
            update={
                "name": "file_write",
                "description": "Write file",
                "risk": "medium",
                "side_effect": "reversible_write",
            }
        ),
        _file_write,
    )
    runner = FakeSingleStepRunner(
        provider=_PlanApprovalThenWriteProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    policy = ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        metadata={"force_planning": {"enabled": True}},
    )
    paused = await runner.run(
        AgentRunInput(
            input="write with plan",
            run_id="run_force_plan_resume",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=policy,
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_force_plan_resume",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=policy,
        )
    )

    assert resumed.status.value == "completed"
    assert writes == [{"path": "x.txt", "content": "ok"}]
    assert resumed.metadata["approved_plan"]["plan_id"] == "plan_force_1"


@pytest.mark.asyncio
async def test_plan_approval_can_end_at_external_execution_handoff() -> None:
    """A host-owned continuation must not execute the source run a second time."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    writes: list[dict[str, object]] = []

    async def _file_write(args):
        writes.append(dict(args))
        return {"summary": "wrote"}

    registry.register(
        danger_tool_manifest().model_copy(update={"name": "file_write"}),
        _file_write,
    )
    provider = _PlanApprovalThenWriteProvider()
    checkpoints = InMemoryCheckpointStore()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    policy = ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        metadata={"force_planning": {"enabled": True}},
    )
    paused = await runner.run(
        AgentRunInput(
            input="write with a host-owned continuation",
            run_id="run_plan_external_handoff",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=policy,
        )
    )
    assert paused.interrupt is not None

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_plan_external_handoff",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                approved_prompts=list(paused.interrupt.proposed_prompts),
                metadata={"plan_execution_handoff": "external"},
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=policy,
        )
    )

    assert resumed.status.value == "completed"
    assert resumed.terminal_reason.value == "external_execution_handoff"
    assert provider.calls == 1
    assert writes == []
    latest = checkpoints.latest("run_plan_external_handoff")
    assert latest is not None
    assert latest.state.metadata["approved_prompts"][0]["tool_name"] == "file_write"


@pytest.mark.asyncio
async def test_runner_resume_edit_applies_edited_args() -> None:
    """Edit resume should execute approved call with edited args."""
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_edit",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_hitl_edit",
            resume=ResumeCommand(
                interrupt_id=(
                    paused.interrupt.interrupt_id if paused.interrupt else "missing"
                ),
                action=ResumeAction.EDIT,
                edited_tool_args={"target": "edited"},
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    assert calls == [{"target": "edited"}]


@pytest.mark.asyncio
async def test_runner_resume_reject_and_cancel_are_terminal() -> None:
    """Reject/cancel resume actions should terminate deterministically."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    checkpoint_store = InMemoryCheckpointStore()
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=checkpoint_store,
        event_log=event_log,
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_terminal",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.interrupt is not None
    rejected = await runner.run(
        AgentRunInput(
            run_id="run_hitl_terminal",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
            ),
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert rejected.status.value == "failed"
    assert rejected.terminal_reason.value == "approval_rejected"

    paused_again = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_cancel",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    cancelled = await runner.run(
        AgentRunInput(
            run_id="run_hitl_cancel",
            resume=ResumeCommand(
                interrupt_id=(
                    paused_again.interrupt.interrupt_id
                    if paused_again.interrupt
                    else "missing"
                ),
                action=ResumeAction.CANCEL,
            ),
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert cancelled.status.value == "cancelled"
    assert cancelled.terminal_reason.value == "cancelled_by_user"


@pytest.mark.asyncio
async def test_runner_resume_clarify_continues_with_clarification() -> None:
    """Clarify resume should continue run and include clarification in metadata."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_clarify",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_hitl_clarify",
            resume=ResumeCommand(
                interrupt_id=(
                    paused.interrupt.interrupt_id if paused.interrupt else "missing"
                ),
                action=ResumeAction.CLARIFY,
                message="Use safer approach",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.NO_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    assert any(event.type.value == "run_resumed" for event in resumed.events)


@pytest.mark.asyncio
async def test_plan_clarify_requires_revised_approval_artifact() -> None:
    """A prose restatement cannot terminally satisfy plan refinement."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    provider = _PlanRefinementProvider()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    initial = await runner.run(
        AgentRunInput(
            input="Run a broad assessment",
            run_id="run_plan_refinement",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert initial.status.value == "paused"
    assert initial.interrupt is not None

    revised = await runner.run(
        AgentRunInput(
            run_id="run_plan_refinement",
            resume=ResumeCommand(
                interrupt_id=initial.interrupt.interrupt_id,
                action=ResumeAction.CLARIFY,
                message="Do passive checks before active checks",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert provider.calls == 3
    revised_exit = next(
        tool
        for tool in provider.requests[1].tools
        if tool.get("function", {}).get("name") == "exit_plan_mode_v2"
    )
    assert set(revised_exit["function"]["parameters"]["required"]) >= {
        "content",
        "requested_tools",
        "target_urls",
    }
    assert "plan" not in revised_exit["function"]["parameters"]["properties"]
    assert revised.status.value == "paused"
    assert revised.interrupt is not None
    plan = revised.interrupt.proposed_action["plan_approval"]
    assert plan["plan_id"] == "plan_revised"
    assert "Passive check" in plan["content"]
    assert "plan_refinement_required" not in revised.metadata


@pytest.mark.asyncio
async def test_plan_clarify_fails_closed_after_bounded_prose_retries() -> None:
    """Repeated prose cannot turn a pending plan refinement into success."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    provider = _PlanRefinementProvider(always_prose=True)
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    initial = await runner.run(
        AgentRunInput(
            input="Run a broad assessment",
            run_id="run_plan_refinement_fail_closed",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert initial.interrupt is not None

    failed = await runner.run(
        AgentRunInput(
            run_id="run_plan_refinement_fail_closed",
            resume=ResumeCommand(
                interrupt_id=initial.interrupt.interrupt_id,
                action=ResumeAction.CLARIFY,
                message="Use a safer order",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert provider.calls == 4
    assert failed.status.value == "failed"
    assert failed.terminal_reason.value == "guardrail_blocked"


@pytest.mark.asyncio
async def test_plan_clarify_keeps_execution_locked_until_revised_approval() -> None:
    """Invalid refinement attempts cannot reopen ordinary tool execution."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    executed: list[dict[str, object]] = []

    async def _danger(args: dict[str, object]) -> dict[str, object]:
        executed.append(args)
        return {"summary": "danger executed"}

    registry.register(danger_tool_manifest(), _danger)
    provider = _MisbehavingPlanRefinementProvider()
    checkpoint_store = InMemoryCheckpointStore()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoint_store,
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    initial = await runner.run(
        AgentRunInput(
            input="Run a broad assessment",
            run_id="run_plan_refinement_lockdown",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert initial.interrupt is not None

    revised = await runner.run(
        AgentRunInput(
            run_id="run_plan_refinement_lockdown",
            resume=ResumeCommand(
                interrupt_id=initial.interrupt.interrupt_id,
                action=ResumeAction.CLARIFY,
                message="Do passive checks before active checks",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert provider.calls == 5
    assert all(
        {tool.get("function", {}).get("name") for tool in request.tools}
        == {"exit_plan_mode_v2"}
        for request in provider.requests[1:]
    )
    assert executed == []
    assert revised.status.value == "paused"
    assert revised.interrupt is not None
    assert revised.interrupt.proposed_action["plan_approval"]["plan_id"] == (
        "plan_revised"
    )
    assert "plan_refinement_required" not in revised.metadata
    checkpoint = checkpoint_store.latest("run_plan_refinement_lockdown")
    assert checkpoint is not None
    assert checkpoint.state.run_input.tool_policy.allowed_tools is None


@pytest.mark.asyncio
async def test_runner_resume_rejects_mismatched_interrupt_id() -> None:
    """Runtime should fail when resume interrupt_id mismatches pending payload."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_bad_id",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    with pytest.raises(
        MissingCheckpointError, match="Checkpoint 'interrupt_other' not found"
    ):
        await runner.run(
            AgentRunInput(
                run_id="run_hitl_bad_id",
                resume=ResumeCommand(
                    interrupt_id="interrupt_other",
                    action=ResumeAction.APPROVE,
                ),
                agent_id="agent",
                graph_preset="single_react",
            )
        )


@pytest.mark.asyncio
async def test_runner_resume_requires_pending_interrupt() -> None:
    """Runtime should reject resume when checkpoint has no pending interrupt."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    completed = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_no_pending",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert completed.status.value == "completed"
    with pytest.raises(RuntimeExecutionError, match="requires pending interrupt"):
        await runner.run(
            AgentRunInput(
                run_id="run_no_pending",
                resume=ResumeCommand(
                    interrupt_id=(
                        completed.checkpoint.checkpoint_id
                        if completed.checkpoint
                        else "missing"
                    ),
                    action=ResumeAction.APPROVE,
                ),
                agent_id="agent",
                graph_preset="single_react",
            )
        )
