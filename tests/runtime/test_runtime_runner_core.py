"""Core runtime runner tests (resume, limits, tool stage integration)."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts.enums import (
    ApprovalMode,
    ResumeAction,
    RuntimeEventType,
    SideEffectClass,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolTrace
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    ToolExecutionResult,
    fake_noop_tool_executor,
)
from agent_driver.runtime.errors import RuntimeExecutionError


class _SlowProvider(FakeProvider):
    """Provider that blocks longer than the run deadline."""

    async def complete(self, request):  # noqa: ANN001
        await asyncio.sleep(10)
        return await super().complete(request)


@pytest.mark.asyncio
async def test_fake_single_step_runner_persists_events_and_checkpoint() -> None:
    """Runner should produce output, events, and checkpoint in one step."""
    provider = FakeProvider(response_text="runner answer")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    run_input = AgentRunInput(
        input="hello runner",
        messages=[ChatMessage(role="user", content="hello runner")],
        run_id="run_test_runtime",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    output = await runner.run(run_input)

    assert output.answer == "runner answer"
    assert output.checkpoint is not None
    assert output.status.value == "completed"
    run_events = events.list_for_run("run_test_runtime")
    assert len(run_events) >= 2
    assert any(event.type.value == "run_completed" for event in run_events)


@pytest.mark.asyncio
async def test_single_agent_runner_resume_after_injected_failure() -> None:
    """Runner should resume from checkpoint after injected step failure."""
    provider = FakeProvider(response_text="resume answer")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    failing = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(fail_after_step="llm_call"),
    )
    run_input = AgentRunInput(
        input="hello runner",
        run_id="run_resume_1",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    with pytest.raises(RuntimeExecutionError):
        await failing.run(run_input)

    latest = checkpoints.latest("run_resume_1")
    assert latest is not None
    resume_runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    with pytest.raises(RuntimeExecutionError):
        await resume_runner.run(
            AgentRunInput(
                resume=ResumeCommand(
                    interrupt_id=latest.ref.checkpoint_id, action=ResumeAction.APPROVE
                ),
                agent_id="agent-test",
                graph_preset="single_react",
            )
        )


@pytest.mark.asyncio
async def test_single_agent_runner_cancellation() -> None:
    """Runner should emit cancelled terminal state when probe is set."""
    provider = FakeProvider(response_text="ignored")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(cancellation_probe=lambda: True),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_cancel_1",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "cancelled"
    assert output.terminal_reason.value == "cancelled_by_user"


@pytest.mark.asyncio
async def test_single_agent_runner_deadline_timeout() -> None:
    """Runner should return timed_out when deadline is exceeded."""
    provider = FakeProvider(response_text="slow")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_deadline_1",
            agent_id="agent-test",
            graph_preset="single_react",
            deadline_seconds=0.000001,
        )
    )
    assert output.status.value == "timed_out"
    assert output.terminal_reason.value == "deadline_exceeded"


@pytest.mark.asyncio
async def test_single_agent_runner_deadline_interrupts_blocking_step() -> None:
    """Run deadline should cancel an in-flight provider call, not wait forever."""
    provider = _SlowProvider(response_text="too late")
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=events,
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_deadline_blocking_step",
            agent_id="agent-test",
            graph_preset="single_react",
            deadline_seconds=0.01,
        )
    )
    assert output.status.value == "timed_out"
    assert output.terminal_reason.value == "deadline_exceeded"
    assert any(
        event.type.value == "run_failed"
        and event.payload.get("reason") == "deadline_exceeded"
        for event in events.list_for_run("run_deadline_blocking_step")
    )


@pytest.mark.asyncio
async def test_single_agent_runner_max_steps_exceeded() -> None:
    """Runner should fail when max_steps budget is reached."""
    provider = FakeProvider(response_text="hello")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        # Isolate pure cap enforcement; the forced-final grace window (default
        # on) is covered separately in test_budget_grace.
        config=RunnerConfig(budget_grace_enabled=False),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_steps_1",
            agent_id="agent-test",
            graph_preset="single_react",
            max_steps=1,
        )
    )
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "max_steps_exceeded"


@pytest.mark.asyncio
async def test_fake_tool_executor_is_used_by_runner() -> None:
    """Runner should invoke custom tool executor in tool stage."""
    calls = {"count": 0}

    async def _counting_executor(run_input: AgentRunInput, llm_response: LlmResponse):
        calls["count"] += 1
        return await fake_noop_tool_executor(run_input, llm_response)

    provider = FakeProvider(response_text="tools")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(tool_executor=_counting_executor),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_tools_1",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "completed"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_policy_profile_absent_does_not_emit_policy_runtime_decisions() -> None:
    provider = FakeProvider(response_text="plain answer")
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=events,
    )

    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_policy_absent",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )

    decisions = [
        event
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("redacted_metadata", {}).get(
            "policy_observe_projection"
        )
    ]
    assert decisions == []


@pytest.mark.asyncio
async def test_observe_policy_profile_emits_skipped_runtime_decision() -> None:
    provider = FakeProvider(response_text="answer without sources")
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=events,
    )

    output = await runner.run(
        AgentRunInput(
            input="answer with sources",
            run_id="run_policy_observe_runtime",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-observe",
                    "mode": "observe",
                    "enabled_policy_ids": ["required_source_evidence"],
                    "required_evidence": ["source_evidence"],
                }
            },
        )
    )

    decisions = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("policy_id") == "required_source_evidence"
    ]
    assert output.status.value == "completed"
    assert decisions
    assert decisions[0]["status"] == "skipped"
    assert decisions[0]["action"] == "mark_blocked"
    assert decisions[0]["redacted_metadata"]["selected_policy_action"] == (
        "mark_blocked"
    )


@pytest.mark.asyncio
async def test_warn_policy_profile_emits_warning_without_changing_answer() -> None:
    provider = FakeProvider(response_text="answer without sources")
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=events,
    )

    output = await runner.run(
        AgentRunInput(
            input="answer with sources",
            run_id="run_policy_warn_runtime",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-warn",
                    "mode": "warn",
                    "enabled_policy_ids": ["required_source_evidence"],
                    "required_evidence": ["source_evidence"],
                }
            },
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "answer without sources"
    assert any(
        event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("status") == "applied"
        and event.payload.get("action") == "warn"
        and event.payload.get("policy_id") == "required_source_evidence"
        and event.payload.get("redacted_metadata", {}).get(
            "selected_policy_action"
        )
        == "mark_blocked"
        for event in output.events
    )
    assert any(
        event.type == RuntimeEventType.WARNING
        and event.payload.get("source") == "harness_policy"
        for event in output.events
    )


@pytest.mark.asyncio
async def test_enforce_required_source_evidence_blocks_false_success() -> None:
    provider = FakeProvider(response_text="answer without sources")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="answer with sources",
            run_id="run_policy_required_source_enforce",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-required-source",
                    "mode": "enforce",
                    "enabled_policy_ids": ["required_source_evidence"],
                    "required_evidence": ["source_evidence"],
                }
            },
        )
    )

    decisions = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("policy_id") == "required_source_evidence"
    ]
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "guardrail_blocked"
    assert decisions
    assert decisions[0]["action"] == "mark_blocked"
    assert decisions[0]["status"] == "applied"
    assert any(event.type == RuntimeEventType.RUN_FAILED for event in output.events)
    assert not any(
        event.type == RuntimeEventType.RUN_COMPLETED for event in output.events
    )


@pytest.mark.asyncio
async def test_enforce_required_source_evidence_allows_observed_sources() -> None:
    provider = FakeProvider(response_text="answer with sources")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="answer with sources",
            run_id="run_policy_required_source_present",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "source_evidence": [
                    {
                        "source_id": "source_1",
                        "source_type": "web",
                        "canonical_url": "https://example.com",
                    }
                ],
                "harness_policy_profile": {
                    "profile_id": "test-required-source",
                    "mode": "enforce",
                    "enabled_policy_ids": ["required_source_evidence"],
                    "required_evidence": ["source_evidence"],
                },
            },
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "answer with sources"
    assert any(event.type == RuntimeEventType.RUN_COMPLETED for event in output.events)


@pytest.mark.asyncio
async def test_enforce_workbook_context_blocks_false_success() -> None:
    provider = FakeProvider(response_text="workbook answer without context")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="answer from the workbook",
            run_id="run_policy_workbook_context_missing",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-workbook-context",
                    "mode": "enforce",
                    "enabled_policy_ids": ["workbook_context_required"],
                    "required_evidence": ["workbook_context"],
                }
            },
        )
    )

    decisions = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("policy_id") == "workbook_context_required"
    ]
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "guardrail_blocked"
    assert decisions
    assert decisions[0]["action"] == "mark_blocked"
    assert decisions[0]["status"] == "applied"
    assert decisions[0]["required_evidence"] == ["workbook_context"]
    assert any(event.type == RuntimeEventType.RUN_FAILED for event in output.events)
    assert not any(
        event.type == RuntimeEventType.RUN_COMPLETED for event in output.events
    )


@pytest.mark.asyncio
async def test_enforce_workbook_context_allows_observed_context() -> None:
    provider = FakeProvider(response_text="workbook answer with context")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="answer from the workbook",
            run_id="run_policy_workbook_context_present",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "context_provenance": [
                    {
                        "context_id": "workbook_context_1",
                        "kind": "workbook",
                        "source_ref": "workbook://active",
                    }
                ],
                "harness_policy_profile": {
                    "profile_id": "test-workbook-context",
                    "mode": "enforce",
                    "enabled_policy_ids": ["workbook_context_required"],
                    "required_evidence": ["workbook_context"],
                },
            },
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "workbook answer with context"
    assert any(event.type == RuntimeEventType.RUN_COMPLETED for event in output.events)


@pytest.mark.asyncio
async def test_enforce_artifact_provenance_blocks_false_success() -> None:
    provider = FakeProvider(response_text="report without artifact")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="write the research report",
            run_id="run_policy_artifact_missing",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-artifact-provenance",
                    "mode": "enforce",
                    "enabled_policy_ids": ["artifact_provenance_required"],
                    "required_evidence": ["artifact_provenance"],
                }
            },
        )
    )

    decisions = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("policy_id") == "artifact_provenance_required"
    ]
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "guardrail_blocked"
    assert decisions
    assert decisions[0]["action"] == "mark_blocked"
    assert decisions[0]["status"] == "applied"
    assert decisions[0]["required_evidence"] == ["artifact_provenance"]
    assert any(event.type == RuntimeEventType.RUN_FAILED for event in output.events)
    assert not any(
        event.type == RuntimeEventType.RUN_COMPLETED for event in output.events
    )


@pytest.mark.asyncio
async def test_enforce_artifact_provenance_allows_observed_artifact() -> None:
    provider = FakeProvider(response_text="report with artifact")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )

    output = await runner.run(
        AgentRunInput(
            input="write the research report",
            run_id="run_policy_artifact_present",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "artifact_provenance": [
                    {
                        "artifact_id": "report_1",
                        "artifact_type": "research_report",
                        "source_tool": "report_writer",
                        "path": "workspace/research/report.md",
                        "preview_status": "available",
                    }
                ],
                "harness_policy_profile": {
                    "profile_id": "test-artifact-provenance",
                    "mode": "enforce",
                    "enabled_policy_ids": ["artifact_provenance_required"],
                    "required_evidence": ["artifact_provenance"],
                },
            },
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "report with artifact"
    assert any(event.type == RuntimeEventType.RUN_COMPLETED for event in output.events)


@pytest.mark.asyncio
async def test_enforce_side_effect_transaction_blocks_false_success() -> None:
    async def _side_effect_executor(
        run_input: AgentRunInput,
        llm_response: LlmResponse,
    ) -> ToolExecutionResult:
        _ = (run_input, llm_response)
        return ToolExecutionResult(
            traces=[
                ToolTrace(
                    step=1,
                    tool_name="excel_apply_edit",
                    tool_call_id="apply_1",
                    status=ToolTraceStatus.COMPLETED,
                    args_summary={"range": "A1"},
                    result_summary="applied workbook edit",
                    risk=ToolRisk.HIGH,
                    side_effect=SideEffectClass.REVERSIBLE_WRITE,
                    approval_mode=ApprovalMode.NEVER,
                )
            ]
        )

    provider = FakeProvider(response_text="edit applied")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=_side_effect_executor),
    )

    output = await runner.run(
        AgentRunInput(
            input="edit the workbook",
            run_id="run_policy_transaction_missing",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "harness_policy_profile": {
                    "profile_id": "test-side-effect-transaction",
                    "mode": "enforce",
                    "enabled_policy_ids": ["side_effect_transaction_required"],
                    "required_evidence": ["side_effect_transactions"],
                    "side_effect_rules": {
                        "require_transaction_projection": True,
                    },
                }
            },
        )
    )

    decisions = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.RUNTIME_DECISION
        and event.payload.get("policy_id") == "side_effect_transaction_required"
    ]
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "guardrail_blocked"
    assert decisions
    assert decisions[0]["action"] == "rollback"
    assert decisions[0]["status"] == "applied"
    assert decisions[0]["required_evidence"] == ["side_effect_transactions"]
    assert decisions[0]["redacted_metadata"]["rollback_available"] is False
    assert any(event.type == RuntimeEventType.RUN_FAILED for event in output.events)
    assert not any(
        event.type == RuntimeEventType.RUN_COMPLETED for event in output.events
    )


@pytest.mark.asyncio
async def test_enforce_side_effect_transaction_allows_projection() -> None:
    async def _transaction_executor(
        run_input: AgentRunInput,
        llm_response: LlmResponse,
    ) -> ToolExecutionResult:
        _ = (run_input, llm_response)
        return ToolExecutionResult(
            traces=[
                ToolTrace(
                    step=1,
                    tool_name="excel_apply_edit",
                    tool_call_id="apply_1",
                    status=ToolTraceStatus.COMPLETED,
                    args_summary={"range": "A1"},
                    result_summary="applied workbook edit",
                    risk=ToolRisk.HIGH,
                    side_effect=SideEffectClass.REVERSIBLE_WRITE,
                    approval_mode=ApprovalMode.NEVER,
                )
            ]
        )

    provider = FakeProvider(response_text="edit applied")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=_transaction_executor),
    )

    output = await runner.run(
        AgentRunInput(
            input="edit the workbook",
            run_id="run_policy_transaction_present",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={
                "side_effect_transactions": [
                    {
                        "transaction_id": "tx_1",
                        "side_effect_class": "workbook-edit",
                        "tool_name": "excel_apply_edit",
                        "target_ref": "workbook://active/A1",
                        "apply_status": "applied",
                        "rollback_ref": "rollback://tx_1",
                        "policy_status": "approved",
                    }
                ],
                "harness_policy_profile": {
                    "profile_id": "test-side-effect-transaction",
                    "mode": "enforce",
                    "enabled_policy_ids": ["side_effect_transaction_required"],
                    "required_evidence": ["side_effect_transactions"],
                    "side_effect_rules": {
                        "require_transaction_projection": True,
                    },
                },
            },
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "edit applied"
    assert any(event.type == RuntimeEventType.RUN_COMPLETED for event in output.events)
