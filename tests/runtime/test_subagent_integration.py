"""Runtime integration tests for subagent flag."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import (
    ChatMessage,
    ControlKind,
    ControlPriority,
    RuntimeEventType,
    SubagentExecutionMode,
    SubagentStatus,
    ToolCall,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.subagents import SubagentRun
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
    wrap_governed_executor,
)
from agent_driver.runtime.control import InMemoryCommandQueueStore
from agent_driver.runtime.single_agent.subagent_stage import _apply_skill_preloads
from agent_driver.subagents import InMemorySubagentMailboxStore, InMemorySubagentStore
from agent_driver.subagents.specs import SubagentGroupSpec, SubagentTaskSpec
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    register_builtin_tools,
)


class _AgentToolSpawnProvider(FakeProvider):
    """Provider that asks parent to spawn one subagent, then finalizes."""

    def __init__(self) -> None:
        super().__init__(response_text="parent done")
        self.calls = 0
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
                            tool_name="agent_tool",
                            tool_call_id="agent_tool_call",
                            args={
                                "task": "summarize child evidence",
                                "description": "Summarize child evidence",
                                "idempotency_key": "child-evidence",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self.calls == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content="child answer"),
                finish_reason=LlmFinishReason.STOP,
                usage=usage,
                provider="fake",
                model="test",
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="parent done"),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model="test",
        )


class _SubagentControlProvider(FakeProvider):
    """Provider that emits one parent-to-child control tool call."""

    def __init__(self, *, tool_name: str, args: dict[str, object]) -> None:
        super().__init__(response_text="parent done")
        self.tool_name = tool_name
        self.args = args
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
                            tool_name=self.tool_name,
                            tool_call_id=f"{self.tool_name}_call",
                            args=self.args,
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="parent done"),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model="test",
        )


@pytest.mark.asyncio
async def test_runtime_without_subagents_keeps_default_flow() -> None:
    """Default runner should not create subagent rows."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_no_sub",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert output.subagent_groups == []
    assert output.subagent_runs == []


@pytest.mark.asyncio
async def test_runtime_with_subagents_executes_group_from_metadata() -> None:
    """Subagent-enabled runtime should produce group/run metadata."""
    store = InMemorySubagentStore()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="parent"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            enable_subagents=True,
            max_child_runs=2,
            subagent_store=store,
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_with_sub",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_subagent_group": {
                        "group_id": "grp_live",
                        "purpose": "fanout",
                        "join_policy": "wait_all",
                        "merge_mode": "append",
                        "tasks": [
                            {
                                "task_id": "t1",
                                "task": "a",
                                "description": "d1",
                                "worker_type": "verifier",
                            },
                            {"task_id": "t2", "task": "b", "description": "d2"},
                        ],
                    }
                }
            },
        )
    )
    assert output.metadata.get("subagent_groups")
    assert output.metadata.get("subagent_runs")
    verifier_run = store.list_runs("run_with_sub")[0]
    assert verifier_run.metadata["handoff"]["worker"]["type"] == "verifier"


@pytest.mark.asyncio
async def test_runtime_with_background_subagents_returns_before_join() -> None:
    """Background subagent backend should let parent complete before child join."""
    event_log = InMemoryEventLog()
    store = InMemorySubagentStore()
    command_queue = InMemoryCommandQueueStore()
    mailbox_store = InMemorySubagentMailboxStore()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="parent"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            enable_subagents=True,
            max_child_runs=2,
            subagent_store=store,
            command_queue_store=command_queue,
            subagent_mailbox_store=mailbox_store,
        ),
    )

    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_background_sub",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_subagent_group": {
                        "group_id": "grp_background",
                        "purpose": "fanout",
                        "join_policy": "wait_all",
                        "merge_mode": "append",
                        "execution_mode": "asyncio_background",
                        "tasks": [
                            {"task_id": "t1", "task": "a", "description": "d1"},
                        ],
                    }
                }
            },
        )
    )

    assert output.answer == "parent"
    assert output.metadata["subagent_groups"][0]["metadata"]["join_state"] == (
        "background_running"
    )
    event_types = [event.type for event in event_log.list_for_run("run_background_sub")]
    assert RuntimeEventType.SUBAGENT_GROUP_JOIN_WAITING in event_types
    for _ in range(20):
        if command_queue.list_pending(run_id="run_background_sub"):
            break
        await asyncio.sleep(0.01)
    assert command_queue.list_pending(run_id="run_background_sub")[0].priority == (
        ControlPriority.LATER
    )
    assert (
        mailbox_store.list_pending(parent_run_id="run_background_sub")[0].payload[
            "status"
        ]
        == "completed"
    )


@pytest.mark.asyncio
async def test_runtime_with_subagents_executes_group_from_agent_tool() -> None:
    """agent_tool envelopes should become native runtime subagent groups."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    event_log = InMemoryEventLog()
    command_queue = InMemoryCommandQueueStore()
    mailbox_store = InMemorySubagentMailboxStore()
    provider = _AgentToolSpawnProvider()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            enable_subagents=True,
            max_child_runs=2,
            command_queue_store=command_queue,
            subagent_mailbox_store=mailbox_store,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )

    output = await runner.run(
        AgentRunInput(
            input="delegate this",
            run_id="run_agent_tool_sub",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert output.answer == "parent done"
    assert output.metadata["subagent_groups"]
    assert output.metadata["subagent_groups"][0]["purpose"] == "agent_tool_spawn"
    assert output.metadata["subagent_runs"]
    assert output.metadata["subagent_runs"][0]["task_id"].startswith("subreq_")
    assert provider.requests[-1].tool_choice == "none"
    event_types = [event.type for event in event_log.list_for_run("run_agent_tool_sub")]
    assert RuntimeEventType.SUBAGENT_STARTED in event_types
    assert RuntimeEventType.SUBAGENT_COMPLETED in event_types
    assert RuntimeEventType.COMMAND_QUEUED in event_types
    queued = command_queue.list_pending(run_id="run_agent_tool_sub")
    assert queued[0].kind == ControlKind.ENQUEUE_USER_MESSAGE
    assert queued[0].priority == ControlPriority.LATER
    assert queued[0].source == "subagent_notification"
    assert (
        mailbox_store.list_pending(parent_run_id="run_agent_tool_sub")[0].payload[
            "status"
        ]
        == "completed"
    )


@pytest.mark.asyncio
async def test_send_message_tool_records_subagent_continuation() -> None:
    """send_message_tool should target an existing child context by id."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    store = InMemorySubagentStore()
    mailbox_store = InMemorySubagentMailboxStore()
    store.upsert_run(_running_child_row(parent_run_id="run_child_continue"))
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=_SubagentControlProvider(
            tool_name="send_message_tool",
            args={
                "recipient": "sub_child",
                "message": "please continue with the latest evidence",
                "thread_id": "run_child_continue",
                "channel": "direct",
                "metadata": {"priority": "later"},
            },
        ),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            enable_subagents=True,
            subagent_store=store,
            subagent_mailbox_store=mailbox_store,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )

    output = await runner.run(
        AgentRunInput(
            input="continue child",
            run_id="run_child_continue",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    row = store.list_runs("run_child_continue")[0]
    continuation = row.metadata["continuation_messages"][0]
    assert continuation["message"] == "please continue with the latest evidence"
    assert continuation["metadata"] == {"priority": "later"}
    mailbox_items = mailbox_store.list_pending(parent_run_id="run_child_continue")
    assert mailbox_items[0].payload == {
        "message": "please continue with the latest evidence"
    }
    assert mailbox_items[0].subagent_run_id == "sub_child"
    assert output.metadata["subagent_runs"][0]["subagent_run_id"] == "sub_child"
    event_types = [event.type for event in event_log.list_for_run("run_child_continue")]
    assert RuntimeEventType.CONTROL_APPLIED in event_types


@pytest.mark.asyncio
async def test_task_stop_tool_cancels_existing_subagent_run() -> None:
    """task_stop_tool should mark an existing child row as cancelled."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    store = InMemorySubagentStore()
    store.upsert_run(_running_child_row(parent_run_id="run_child_stop"))
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=_SubagentControlProvider(
            tool_name="task_stop_tool",
            args={
                "task_id": "sub_child",
                "subagent_run_id": "sub_child",
                "status": "killed",
                "reason": "no longer needed",
            },
        ),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            enable_subagents=True,
            subagent_store=store,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )

    output = await runner.run(
        AgentRunInput(
            input="stop child",
            run_id="run_child_stop",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    row = store.list_runs("run_child_stop")[0]
    assert row.status == SubagentStatus.CANCELLED
    assert row.metadata["stop_reason"] == "no longer needed"
    assert output.metadata["subagent_runs"][0]["status"] == "cancelled"
    event_types = [event.type for event in event_log.list_for_run("run_child_stop")]
    assert RuntimeEventType.SUBAGENT_COMPLETED in event_types
    assert RuntimeEventType.CONTROL_APPLIED in event_types


def test_skill_aware_subagent_preload_keeps_only_trusted_viewed_skills(
    tmp_path,
) -> None:
    """Optional subagent skill preload should ignore untrusted invocations."""
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: trusted-research\n---\n# Trusted Research\nFetch sources.",
        encoding="utf-8",
    )
    context = type(
        "Context",
        (),
        {
            "metadata": {
                "skill_invocations": [
                    {
                        "name": "trusted-research",
                        "path": str(skill_path),
                        "digest": "abc",
                        "trusted": True,
                    },
                    {
                        "name": "untrusted",
                        "path": str(skill_path),
                        "digest": "def",
                        "trusted": False,
                    },
                ]
            }
        },
    )()
    group = SubagentGroupSpec(
        group_id="group",
        purpose="research",
        tasks=(
            SubagentTaskSpec(
                task_id="one",
                task="Research one source",
                description="Research one source",
            ),
        ),
        metadata={"skill_preload": "trusted_viewed"},
    )

    updated = _apply_skill_preloads(context, group)

    assert updated.metadata["skill_preload_count"] == 1
    assert "Trusted skill preload" in updated.tasks[0].task
    assert updated.tasks[0].metadata["skill_preloads"][0]["name"] == (
        "trusted-research"
    )


def _running_child_row(*, parent_run_id: str) -> SubagentRun:
    return SubagentRun(
        subagent_run_id="sub_child",
        parent_run_id=parent_run_id,
        parent_attempt_id="attempt_1",
        child_run_id="child_run_1",
        task_id="task_child",
        task_type="agent",
        description="existing child task",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.RUNNING,
        metadata={"idempotency_key": "child-key"},
    )
