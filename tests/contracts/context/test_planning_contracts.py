"""Planning and trimming contracts tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ContextBudget,
    ObservationMemory,
    ObservationProvenance,
    PlanApprovalPayload,
    PlanArtifact,
    PlanningModeState,
    ObservationSource,
    ObservationTrust,
    PlanningState,
    PlanningStep,
    PlanningTodoStatus,
    TodoState,
    TrimAction,
    TrimAuditRecord,
    TrimmedContext,
)


def test_planning_state_carries_todos_and_latest_step() -> None:
    """Planning state should preserve todos and latest planning step."""
    state = PlanningState(
        run_id="run_1",
        todos=[
            TodoState(
                todo_id="todo_1", content="check", status=PlanningTodoStatus.PENDING
            )
        ],
        latest_step=PlanningStep(
            step_id="step_1",
            facts_given=["A"],
            facts_learned=["B"],
            facts_to_lookup=["C"],
            facts_to_derive=["D"],
            next_plan="Do next",
        ),
    )
    restored = PlanningState.model_validate(state.model_dump(mode="json"))
    assert restored.todos[0].status == PlanningTodoStatus.PENDING
    assert restored.latest_step is not None


def test_plan_artifact_requires_approval_timestamp_when_approved() -> None:
    """Approved plan artifacts should carry approval metadata."""
    with pytest.raises(ValidationError):
        PlanArtifact(
            plan_id="plan_1",
            run_id="run_1",
            agent_id="agent",
            content="Do the work",
            content_hash="hash",
            status=PlanningModeState.APPROVED,
        )


def test_plan_approval_payload_round_trip() -> None:
    """Plan approval payload should remain JSON-contract friendly."""
    payload = PlanApprovalPayload(
        plan_id="plan_1",
        run_id="run_1",
        agent_id="agent",
        content="1. Inspect\n2. Change",
        content_hash="hash",
        metadata={"source": "exit_plan_mode_v2"},
    )
    restored = PlanApprovalPayload.model_validate(payload.model_dump(mode="json"))
    assert restored.plan_id == "plan_1"
    assert restored.metadata["source"] == "exit_plan_mode_v2"


def test_trimmed_context_rejects_negative_budget() -> None:
    """Context budget should reject negative limits."""
    with pytest.raises(ValidationError):
        ContextBudget(max_chars=-1)


def test_observation_memory_round_trip() -> None:
    """Observation memory should preserve provenance and trust labels."""
    observation = ObservationMemory(
        observation_id="obs_1",
        text_preview="preview",
        provenance=ObservationProvenance(
            source=ObservationSource.TOOL_STDOUT,
            trust=ObservationTrust.MEDIUM,
            tool_name="lookup",
        ),
        truncated=True,
        original_length=123,
    )
    trimmed = TrimmedContext(
        prompt_messages=[{"role": "system", "content": "x"}],
        audit=[
            TrimAuditRecord(
                record_id="rec_1", kind="observation", action=TrimAction.KEPT
            )
        ],
    )
    assert ObservationMemory.model_validate(
        observation.model_dump(mode="json")
    ).truncated
    assert (
        TrimmedContext.model_validate(trimmed.model_dump(mode="json")).audit[0].action
        == TrimAction.KEPT
    )
