"""Interrupt and subagent contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ArtifactKind,
    ArtifactRef,
    MergeProvenance,
    ParentStateWriteMode,
    ResumeAction,
    ResumeCommand,
    SubagentExecutionMode,
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
