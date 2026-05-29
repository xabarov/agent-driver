"""Tests for optional structured extraction adapter boundaries."""

from __future__ import annotations

import pytest

from agent_driver.contracts import ControlKind, ControlPriority
from agent_driver.structured import (
    StructuredExtractionError,
    parse_steering_text,
    validate_plan_artifact_payload,
)


def test_parse_steering_text_enqueues_message() -> None:
    request = parse_steering_text("focus on the risks", run_id="run_1")

    assert request.kind == ControlKind.ENQUEUE_USER_MESSAGE
    assert request.run_id == "run_1"
    assert request.payload == {"message": "focus on the risks"}


def test_parse_steering_text_switches_model() -> None:
    request = parse_steering_text("switch model openai/gpt-4.1-mini", run_id="run_1")

    assert request.kind == ControlKind.SET_MODEL
    assert request.priority == ControlPriority.NEXT
    assert request.payload == {"model": "openai/gpt-4.1-mini"}


def test_parse_steering_text_interrupts_now() -> None:
    request = parse_steering_text("stop", run_id="run_1")

    assert request.kind == ControlKind.INTERRUPT
    assert request.priority == ControlPriority.NOW
    assert request.payload["reason"] == "user_requested_interrupt"


def test_validate_plan_artifact_payload_accepts_draft() -> None:
    draft = validate_plan_artifact_payload(
        {
            "scope": "Update chat steering",
            "steps": [
                {
                    "title": "Persist controls",
                    "action": "Store queue state in session metadata",
                    "verification": "Backend and frontend tests pass",
                }
            ],
            "risks": ["stale queue state"],
            "verification": ["pytest", "vitest"],
            "requested_permissions": ["write"],
        }
    )

    assert draft.scope == "Update chat steering"
    assert draft.steps[0].title == "Persist controls"
    assert draft.requested_permissions == ["write"]


def test_validate_plan_artifact_payload_raises_structured_failure() -> None:
    with pytest.raises(StructuredExtractionError) as exc:
        validate_plan_artifact_payload({"scope": "", "steps": []})

    failure = exc.value.failure
    assert failure.error_kind == "validation_error"
    assert failure.as_observation()["kind"] == "structured_extraction_failure"
    assert failure.validation_errors
