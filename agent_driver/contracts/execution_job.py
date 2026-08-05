"""Public validated contracts for reconnectable execution jobs (EPIC-04).

A long-running backend operation that can outlive a transport connection needs
stable identity, bounded ordered events, reconnectable snapshots, truthful
controls, generation fencing, and a SEPARATE teardown proof. These are the typed
request/result/receipt models a job-capable backend exchanges.

Guarantees encoded here:
- event identity is ``(execution_generation, sequence)`` and replay is
  duplicate-tolerant; conflicting content for the same identity is a protocol
  violation;
- ``ExecutionHandle`` carries a start idempotency key so a lost start response
  can be resolved by lookup rather than blindly re-dispatched;
- run cancellation, execution termination, and environment teardown are distinct
  facts — a terminal execution result never implies a released lease, and a
  cancelled run never proves the environment was destroyed;
- nothing here promises exactly-once side effects the backend cannot prove.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.execution import (
    ExecutionCommandResult,
    _reject_secret_like_keys,
)
from agent_driver.contracts.execution_lease import LeaseOwnership
from agent_driver.contracts.validation import ensure_bounded_json_metadata

EXECUTION_JOB_SCHEMA_VERSION = "agent_driver.execution.job.v1"

_MAX_EVENT_TEXT_CHARS = 8000


class ExecutionJobState(str, Enum):
    """Lifecycle state of one execution job. ``INDETERMINATE`` means the dispatch
    or terminal result is unknown and must NOT be auto-redispatched; ``LOST``
    means the observation transport dropped and a reconnect/snapshot is needed."""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INDETERMINATE = "indeterminate"
    LOST = "lost"


class ExecutionEventKind(str, Enum):
    """Category of one bounded job event."""

    PROGRESS = "progress"
    OUTPUT = "output"
    ERROR = "error"
    TERMINAL = "terminal"


class ExecutionControlKind(str, Enum):
    """A requested control. Strength is capability-driven: a backend may accept a
    STOP but only apply a cooperative cancel, and TEARDOWN is a separate fact."""

    CANCEL_COOPERATIVE = "cancel_cooperative"
    STOP = "stop"
    TEARDOWN = "teardown"


class ExecutionReasonCode(str, Enum):
    """Stable, host-facing reason codes for job/control/teardown outcomes."""

    OK = "ok"
    QUEUE_TIMEOUT = "queue_timeout"
    START_TIMEOUT = "start_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    EXECUTION_TIMEOUT = "execution_timeout"
    CONTROL_TIMEOUT = "control_timeout"
    TEARDOWN_TIMEOUT = "teardown_timeout"
    TRANSPORT_LOST = "transport_lost"
    INDETERMINATE_DISPATCH = "indeterminate_dispatch"
    STALE_GENERATION = "stale_generation"
    DUPLICATE_TERMINAL = "duplicate_terminal"
    PROTOCOL_VIOLATION = "protocol_violation"
    UNSUPPORTED = "unsupported"


class ExecutionHandle(ContractModel):
    """Safe, durable, generation-bound reference to one execution job.

    Non-secret and checkpoint-safe. ``idempotency_key`` fixes the start so a lost
    start response is resolved by lookup, never blindly re-dispatched.
    ``execution_generation`` fences stale attempts/deliveries.
    """

    schema_version: str = EXECUTION_JOB_SCHEMA_VERSION
    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    execution_generation: str = Field(min_length=1)
    lease_id: str | None = None

    def fences(self, other: "ExecutionHandle") -> bool:
        """True when ``other`` is the same job at a DIFFERENT (stale) execution
        generation — its late events/results must be fenced."""
        return (
            self.job_id == other.job_id
            and self.execution_generation != other.execution_generation
        )


class ExecutionEvent(ContractModel):
    """One bounded, ordered job event. Identity is ``(execution_generation,
    sequence)``; the same identity must never carry conflicting content."""

    execution_generation: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    kind: ExecutionEventKind
    text: str = Field(default="", max_length=_MAX_EVENT_TEXT_CHARS)
    terminal: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_secret_like_keys(value, field_name="metadata")
        return ensure_bounded_json_metadata(value, field_name="metadata")

    def identity_key(self) -> tuple[str, int]:
        """The stable, duplicate-tolerant event identity."""
        return (self.execution_generation, self.sequence)

    def conflicts_with(self, other: "ExecutionEvent") -> bool:
        """Same identity but different content ⇒ a backend protocol violation."""
        return self.identity_key() == other.identity_key() and (
            self.kind is not other.kind
            or self.text != other.text
            or self.terminal != other.terminal
        )


class ExecutionEventCursor(ContractModel):
    """The last committed observation position. Safe for durable checkpoint
    state; a reconnect resumes from ``last_sequence + 1``."""

    job_id: str = Field(min_length=1)
    execution_generation: str = Field(min_length=1)
    last_sequence: int = Field(default=-1, ge=-1)  # -1 = nothing committed yet


class ExecutionEventPage(ContractModel):
    """A bounded page of events plus the cursor to continue from. ``gap_detected``
    means the backend compacted/dropped history before the requested cursor, so
    the caller must fall back to the terminal snapshot rather than assume
    contiguity."""

    events: tuple[ExecutionEvent, ...] = ()
    next_cursor: ExecutionEventCursor
    gap_detected: bool = False
    complete: bool = False


class ExecutionTerminalSnapshot(ContractModel):
    """The terminal state of a job, resolvable after reconnect/restart WITHOUT
    re-dispatch. A terminal result does NOT imply the lease was released."""

    handle: ExecutionHandle
    state: ExecutionJobState
    exit_code: int | None = None
    result: ExecutionCommandResult | None = None
    indeterminate: bool = False
    reason_code: ExecutionReasonCode | None = None


class ExecutionControlRequest(ContractModel):
    """A requested control against a running job."""

    handle: ExecutionHandle
    kind: ExecutionControlKind


class ExecutionControlReceipt(ContractModel):
    """Truthful, capability-backed control facts. ``accepted`` (the backend took
    the request) and ``applied`` (it actually took effect) are SEPARATE — a
    cooperative-only backend may accept a STOP without applying hard teardown."""

    handle: ExecutionHandle
    kind: ExecutionControlKind
    accepted: bool = False
    applied: bool = False
    execution_terminal: bool = False
    reason_code: ExecutionReasonCode | None = None


class TeardownReceipt(ContractModel):
    """Environment-teardown fact, kept SEPARATE from execution-terminal state.
    ``confirmed`` is only true when the backend proves teardown; a runtime cancel
    never sets it. Ownership decides whether teardown is even attempted."""

    handle: ExecutionHandle
    ownership: LeaseOwnership
    requested: bool = False
    confirmed: bool = False
    reason_code: ExecutionReasonCode | None = None


__all__ = [
    "EXECUTION_JOB_SCHEMA_VERSION",
    "ExecutionJobState",
    "ExecutionEventKind",
    "ExecutionControlKind",
    "ExecutionReasonCode",
    "ExecutionHandle",
    "ExecutionEvent",
    "ExecutionEventCursor",
    "ExecutionEventPage",
    "ExecutionTerminalSnapshot",
    "ExecutionControlRequest",
    "ExecutionControlReceipt",
    "TeardownReceipt",
]
