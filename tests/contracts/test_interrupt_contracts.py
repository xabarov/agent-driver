"""Interrupt and subagent contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ApprovalPayload,
    ArtifactKind,
    ArtifactRef,
    InterruptReason,
    InterruptRequest,
    MergeProvenance,
    ParentStateWriteMode,
    ResumeAction,
    ResumeCommand,
    SubagentExecutionMode,
    SubagentGroup,
    SubagentGroupStatus,
    SubagentJoinPolicy,
    SubagentMergeMode,
    SubagentRun,
    SubagentStatus,
    SubagentTerminalState,
    UsageSummary,
)


def test_resume_command_edit_requires_payload() -> None:
    """Reject edit action without patch payload."""
    with pytest.raises(ValidationError):
        ResumeCommand(interrupt_id="int_1", action=ResumeAction.EDIT)


def test_resume_command_clarify_requires_message() -> None:
    """Reject clarify action without message."""
    with pytest.raises(ValidationError):
        ResumeCommand(interrupt_id="int_1", action=ResumeAction.CLARIFY)


def test_approval_payload_from_interrupt_renders_args_preview() -> None:
    """Approval payload should expose deterministic tool args preview."""
    payload = ApprovalPayload.from_interrupt(
        InterruptRequest(
            interrupt_id="int_1",
            run_id="run_1",
            attempt_id="att_1",
            checkpoint_id="cp_1",
            reason=InterruptReason.APPROVAL_REQUIRED,
            title="Approval required",
            description="Please review tool call",
            risk="high",
            proposed_action={
                "tool_name": "danger",
                "tool_call_id": "call_1",
                "args": {"target": "x"},
            },
            allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
            editable_fields=["args"],
            metadata={"source": "policy"},
        )
    )
    assert payload.tool_name == "danger"
    assert payload.tool_call_id == "call_1"
    assert payload.args_preview == '{"target": "x"}'
    assert payload.interrupt_id == "int_1"
    assert payload.allowed_actions == [ResumeAction.APPROVE, ResumeAction.REJECT]
    assert payload.editable_fields == ["args"]
    assert payload.metadata == {"source": "policy"}


def test_approval_payload_truncates_large_args_preview() -> None:
    """Payload helper should truncate oversized args preview deterministically."""
    payload = ApprovalPayload.from_interrupt(
        InterruptRequest(
            interrupt_id="int_2",
            run_id="run_1",
            attempt_id="att_1",
            checkpoint_id="cp_1",
            reason=InterruptReason.APPROVAL_REQUIRED,
            title="Approval required",
            description="Please review tool call",
            risk="medium",
            proposed_action={"args": {"text": "a" * 80}},
            allowed_actions=[ResumeAction.APPROVE],
            metadata={},
        ),
        args_preview_chars=30,
    )
    assert payload.args_preview is not None
    assert payload.args_preview.endswith("...")


def test_subagent_terminal_requires_terminal_state() -> None:
    """Reject terminal rows without terminal state."""
    with pytest.raises(ValidationError):
        SubagentRun(
            subagent_run_id="sub_1",
            parent_run_id="run_1",
            parent_attempt_id="att_1",
            task_id="task_1",
            task_type="research",
            description="child run",
            execution_mode=SubagentExecutionMode.SYNC,
            fanout_slot=1,
            status=SubagentStatus.COMPLETED,
        )


def test_subagent_completed_requires_output_or_merge_provenance() -> None:
    """Reject completed rows without output or merge metadata."""
    with pytest.raises(ValidationError):
        SubagentRun(
            subagent_run_id="sub_1",
            parent_run_id="run_1",
            parent_attempt_id="att_1",
            task_id="task_1",
            task_type="research",
            description="child run",
            status=SubagentStatus.COMPLETED,
            terminal_state=SubagentTerminalState.SUCCEEDED,
        )


def test_subagent_round_trip_with_merge_provenance() -> None:
    """Round-trip completed subagent rows through JSON payload."""
    row = SubagentRun(
        subagent_run_id="sub_1",
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        task_id="task_1",
        task_type="research",
        description="child run",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.COMPLETED,
        terminal_state=SubagentTerminalState.SUCCEEDED,
        tokens=UsageSummary(input_tokens=10, output_tokens=5),
        output_pointer=ArtifactRef(
            artifact_id="art_1",
            kind=ArtifactKind.SUBAGENT_OUTPUT,
            preview="summary",
        ),
        merge_provenance=MergeProvenance(
            strategy="typed_specialist_results_v3",
            source_kind="specialist_results_v3",
            parent_state_write=ParentStateWriteMode.BOUNDED_APPEND_ONLY,
        ),
    )

    restored = SubagentRun.model_validate(row.model_dump(mode="json"))
    assert restored.status == SubagentStatus.COMPLETED


def test_subagent_group_round_trip() -> None:
    """Round-trip subagent group metadata through JSON payload."""
    group = SubagentGroup(
        group_id="grp_1",
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        merge_mode=SubagentMergeMode.SYNTHESIZE,
        max_parallel=3,
        deadline_seconds=30.0,
        token_budget=1_000,
        cost_budget_usd=0.5,
        child_run_ids=["child_1", "child_2"],
        status=SubagentGroupStatus.RUNNING,
    )
    restored = SubagentGroup.model_validate(group.model_dump(mode="json"))
    assert restored.join_policy == SubagentJoinPolicy.WAIT_ALL
