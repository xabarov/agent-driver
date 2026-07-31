"""Epic 045 C: resume → wait resolution mapping + bounded-deadline liveness."""

from __future__ import annotations

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.wait_for_event import (
    WAIT_TIMEOUT_STATE_KEY,
    WaitForEventStatus,
    wait_for_event_resolution_from_resume,
    wait_for_event_timed_out,
)


def test_clarify_delivers_payload_from_message() -> None:
    res = wait_for_event_resolution_from_resume(
        event_key="build.done", action=ResumeAction.CLARIFY, message="exit 0"
    )
    assert res.status is WaitForEventStatus.DELIVERED
    assert res.payload == {"message": "exit 0"}


def test_clarify_delivers_payload_from_state_patch() -> None:
    res = wait_for_event_resolution_from_resume(
        event_key="build.done",
        action=ResumeAction.CLARIFY,
        state_patch={"exit_code": 0, "artifact": "app.bin"},
    )
    assert res.status is WaitForEventStatus.DELIVERED
    assert res.payload["exit_code"] == 0


def test_cancel_maps_to_cancelled() -> None:
    res = wait_for_event_resolution_from_resume(
        event_key="build.done", action=ResumeAction.CANCEL
    )
    assert res.status is WaitForEventStatus.CANCELLED


def test_timeout_marker_maps_to_timed_out() -> None:
    res = wait_for_event_resolution_from_resume(
        event_key="build.done",
        action=ResumeAction.CLARIFY,
        state_patch={WAIT_TIMEOUT_STATE_KEY: True},
    )
    assert res.status is WaitForEventStatus.TIMED_OUT


def test_deadline_classifier() -> None:
    assert wait_for_event_timed_out(elapsed_seconds=120, deadline_seconds=120) is True
    assert wait_for_event_timed_out(elapsed_seconds=119, deadline_seconds=120) is False
