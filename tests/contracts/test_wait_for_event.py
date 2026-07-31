"""Epic 045 A: wait_for_event contracts + bounded-deadline liveness."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import InterruptReason
from agent_driver.contracts.wait_for_event import (
    DEFAULT_WAIT_DEADLINE_SECONDS,
    MAX_WAIT_DEADLINE_SECONDS,
    WaitForEventRequest,
    WaitForEventResolution,
    WaitForEventStatus,
    clamp_wait_deadline,
)


def test_interrupt_reason_has_wait_for_event() -> None:
    assert InterruptReason.WAIT_FOR_EVENT.value == "wait_for_event"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, DEFAULT_WAIT_DEADLINE_SECONDS),
        (0, DEFAULT_WAIT_DEADLINE_SECONDS),
        (-5, DEFAULT_WAIT_DEADLINE_SECONDS),
        (30, 30),
        (MAX_WAIT_DEADLINE_SECONDS + 10, MAX_WAIT_DEADLINE_SECONDS),
    ],
)
def test_clamp_wait_deadline_is_always_bounded(given, expected) -> None:
    assert clamp_wait_deadline(given) == expected


def test_request_clamps_deadline_on_construction() -> None:
    # A subscription can never be unbounded — the validator clamps it.
    assert WaitForEventRequest(event_key="build.done", deadline_seconds=0).deadline_seconds == (
        DEFAULT_WAIT_DEADLINE_SECONDS
    )
    huge = WaitForEventRequest(event_key="x", deadline_seconds=10**9)
    assert huge.deadline_seconds == MAX_WAIT_DEADLINE_SECONDS


def test_request_requires_event_key() -> None:
    with pytest.raises(ValueError):
        WaitForEventRequest(event_key="   ")


def test_request_defaults() -> None:
    req = WaitForEventRequest(event_key="proc.exit")
    assert req.deadline_seconds == DEFAULT_WAIT_DEADLINE_SECONDS
    assert req.poll_fallback_seconds is None
    assert req.description == ""


def test_resolution_roundtrip() -> None:
    res = WaitForEventResolution(
        event_key="proc.exit",
        status=WaitForEventStatus.DELIVERED,
        payload={"exit_code": 0},
    )
    assert res.status is WaitForEventStatus.DELIVERED
    assert res.model_dump(mode="json")["payload"]["exit_code"] == 0
