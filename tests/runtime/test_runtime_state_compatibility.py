"""Rolling rollback compatibility for strict 0.2.0rc5 state readers."""

from __future__ import annotations

from agent_driver.contracts import AgentRunInput, CheckpointRef, ResumeCommand
from agent_driver.contracts.context import RunContextBudget
from agent_driver.contracts.enums import ResumeAction
from agent_driver.runtime import (
    ROLLBACK_TARGET_0_2_RC5,
    RuntimeState,
    serialize_runtime_state_for_compatibility,
)


def _state() -> RuntimeState:
    return RuntimeState(
        run_input=AgentRunInput(
            input="continue",
            run_id="run_1",
            agent_id="agent",
            graph_preset="single_react",
            context_budget=RunContextBudget(
                input_tokens=180_000,
                output_tokens=30_000,
            ),
            resume=ResumeCommand(
                interrupt_id="int_1",
                action=ResumeAction.APPROVE,
                idempotency_key="approval_1",
                expected_checkpoint_id="chk_1",
                expected_revision=7,
            ),
        ),
        checkpoint=CheckpointRef(
            checkpoint_id="chk_1",
            run_id="run_1",
            attempt_id="attempt_1",
            graph_id="single_agent_runtime",
            created_at="2026-08-03T00:00:00Z",
            state_version="1",
            revision=7,
            storage_backend="memory",
        ),
    )


def test_new_reader_accepts_rc5_payload_with_additive_fields_absent() -> None:
    payload = _state().model_dump(mode="json")
    payload["checkpoint"].pop("revision")
    payload["run_input"].pop("context_budget")
    for field in ("idempotency_key", "expected_checkpoint_id", "expected_revision"):
        payload["run_input"]["resume"].pop(field)

    restored = RuntimeState.model_validate(payload)
    assert restored.checkpoint is not None
    assert restored.checkpoint.revision == 0
    assert restored.run_input.context_budget is None
    assert restored.run_input.resume is not None
    assert restored.run_input.resume.expected_revision is None


def test_compatibility_writer_downconverts_only_known_additive_fields() -> None:
    result = serialize_runtime_state_for_compatibility(
        _state(), target=ROLLBACK_TARGET_0_2_RC5
    )
    payload = result.payload

    assert "revision" not in payload["checkpoint"]
    assert "context_budget" not in payload["run_input"]
    assert payload["run_input"]["app_metadata"]["context_budget"] == {
        "input_tokens": 180_000,
        "output_tokens": 30_000,
    }
    rc5_resume_fields = set(ResumeCommand.model_fields) - {
        "idempotency_key",
        "expected_checkpoint_id",
        "expected_revision",
    }
    assert set(payload["run_input"]["resume"]) == rc5_resume_fields
    rc5_run_input_fields = set(AgentRunInput.model_fields) - {"context_budget"}
    assert set(payload["run_input"]) == rc5_run_input_fields
    assert result.removed_paths == (
        "checkpoint.revision",
        "run_input.context_budget",
        "run_input.resume.expected_checkpoint_id",
        "run_input.resume.expected_revision",
        "run_input.resume.idempotency_key",
    )
    assert result.transformed_paths == (
        "run_input.context_budget->run_input.app_metadata.context_budget",
    )
    assert all("continue" not in path for path in result.removed_paths)


def test_compatibility_payload_remains_readable_by_new_release() -> None:
    result = serialize_runtime_state_for_compatibility(
        _state(), target=ROLLBACK_TARGET_0_2_RC5
    )
    restored = RuntimeState.model_validate(result.payload)
    assert restored.checkpoint is not None
    assert restored.checkpoint.revision == 0
    assert restored.run_input.context_budget is None
    assert restored.run_input.app_metadata["context_budget"]["input_tokens"] == 180_000
