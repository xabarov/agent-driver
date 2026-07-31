"""Event-driven wait (park-on-event) contracts (epic 045 A).

A run that must wait for an external event (a background process to exit, a webhook,
a file to appear, a queue message) should PARK on the event rather than poll for it in
a tool loop — polling burns steps (the 019 per-turn caps punish it) and re-reads the
whole context every poll. The engine parks the run via the existing interrupt/resume
machinery with a dedicated ``WAIT_FOR_EVENT`` reason; the host subscribes to the real
event source and delivers a ``ResumeCommand`` when it fires.

Liveness (tie-in to epic 041): a subscription is ALWAYS bounded by a deadline. A wait
that never fires degrades to a ``timed_out`` resolution, never an infinite hang. The
engine clamps the deadline into ``[1, MAX_WAIT_DEADLINE_SECONDS]`` and applies a default
when the caller leaves it open.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum
from agent_driver.contracts.validation import ensure_json_serializable

# A subscription can never be unbounded (liveness): default when unset, hard ceiling.
DEFAULT_WAIT_DEADLINE_SECONDS = 3600
MAX_WAIT_DEADLINE_SECONDS = 86_400


def clamp_wait_deadline(deadline_seconds: float | None) -> int:
    """Return a bounded deadline in seconds — never unbounded (epic 045 liveness)."""
    if deadline_seconds is None or deadline_seconds <= 0:
        return DEFAULT_WAIT_DEADLINE_SECONDS
    return int(min(deadline_seconds, MAX_WAIT_DEADLINE_SECONDS))


class WaitForEventStatus(StrEnum):
    """Resolution status of a parked event wait."""

    DELIVERED = "delivered"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class WaitForEventRequest(ContractModel):
    """A subscription the run parks on until an external event fires."""

    event_key: str
    # Always bounded (see clamp_wait_deadline) — a wait can never hang forever.
    deadline_seconds: int = DEFAULT_WAIT_DEADLINE_SECONDS
    # Optional bounded poll cadence the host MAY use as a fallback event source when
    # it has no push channel; the engine only records it, it does not poll itself.
    poll_fallback_seconds: int | None = None
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_key")
    @classmethod
    def validate_event_key(cls, value: str) -> str:
        """A subscription must name the event it waits on."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("event_key must be a non-empty string")
        return value.strip()

    @field_validator("deadline_seconds")
    @classmethod
    def validate_deadline(cls, value: int) -> int:
        """Clamp the deadline so a parked wait is always bounded."""
        return clamp_wait_deadline(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="wait_for_event metadata")


class WaitForEventResolution(ContractModel):
    """The outcome that wakes a parked run."""

    event_key: str
    status: WaitForEventStatus
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure payload stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="wait_for_event payload")


# Marker a host sets in a resume state_patch to signal the deadline elapsed with no
# event, so the wake resolves as a bounded timeout rather than a delivered payload.
WAIT_TIMEOUT_STATE_KEY = "wait_for_event_timed_out"


def wait_for_event_timed_out(
    *, elapsed_seconds: float, deadline_seconds: float
) -> bool:
    """Whether a parked wait has passed its (bounded) deadline (epic 045 liveness)."""
    return elapsed_seconds >= deadline_seconds


def wait_for_event_resolution_from_resume(
    *,
    event_key: str,
    action: Any,
    message: str | None = None,
    state_patch: dict[str, Any] | None = None,
) -> WaitForEventResolution:
    """Map a resume command into a wait resolution (epic 045 C).

    ``CANCEL`` → cancelled. ``CLARIFY`` carrying the timeout marker → timed_out
    (the liveness backstop fired: the wait never delivered). Any other ``CLARIFY``
    → delivered, with the event payload taken from ``state_patch`` (or the message).
    """
    action_value = str(getattr(action, "value", action) or "")
    patch = dict(state_patch or {})
    if action_value == "cancel":
        return WaitForEventResolution(
            event_key=event_key,
            status=WaitForEventStatus.CANCELLED,
            payload=patch,
        )
    if patch.get(WAIT_TIMEOUT_STATE_KEY):
        return WaitForEventResolution(
            event_key=event_key,
            status=WaitForEventStatus.TIMED_OUT,
            payload=patch,
        )
    payload = patch or ({"message": message} if message else {})
    return WaitForEventResolution(
        event_key=event_key,
        status=WaitForEventStatus.DELIVERED,
        payload=payload,
    )


__all__ = [
    "DEFAULT_WAIT_DEADLINE_SECONDS",
    "MAX_WAIT_DEADLINE_SECONDS",
    "WAIT_TIMEOUT_STATE_KEY",
    "WaitForEventRequest",
    "WaitForEventResolution",
    "WaitForEventStatus",
    "clamp_wait_deadline",
    "wait_for_event_resolution_from_resume",
    "wait_for_event_timed_out",
]
