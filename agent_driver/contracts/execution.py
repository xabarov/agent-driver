"""Public validated contracts for the execution-backend seam (EPIC-01).

These are the backend-neutral, JSON-safe request/result/identity/failure models
that the ``ExecutionBackend`` protocol exchanges. EPIC-01 ships the minimal
command + text-file surface; lease, capability, event, and control vocabulary is
reserved here (defined but not yet driven by protocol methods) so later epics add
methods without re-versioning the wire shape. See
``docs/epics/execution-backend/TARGET_CONTRACT.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_bounded_json_metadata

EXECUTION_SCHEMA_VERSION = "agent_driver.execution.v1"


class ExecutionTerminalState(str, Enum):
    """Typed terminal state of one backend execution."""

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class CapabilityState(str, Enum):
    """State of one observed backend capability. Missing evidence is ``unknown``,
    never ``supported`` (reserved for EPIC-02 capability snapshots)."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class ExecutionIdentity(ContractModel):
    """Stable identity carried by every execution request and result.

    Enough to fence a response from an obsolete attempt/generation. ``request_id``
    is the idempotency key for one mutating request.
    """

    backend_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class ExecutionBounds(ContractModel):
    """Applied output/size limits for one execution (bounds live with the caller;
    echoed here so a result can report what was enforced)."""

    max_output_chars: int = Field(ge=0)
    max_bytes: int | None = Field(default=None, ge=0)


class ArtifactRef(ContractModel):
    """Content-addressed reference to full output kept outside model context.

    A reference is not proof the content was inspected.
    """

    artifact_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    media_type: str | None = None
    backend_id: str | None = None
    execution_id: str | None = None


class ExecutionCommandRequest(ContractModel):
    """An already-authorized, bounded command for the backend to run."""

    identity: ExecutionIdentity
    command: str
    cwd: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    max_output_chars: int = Field(ge=0)


class ExecutionCommandResult(ContractModel):
    """Typed result of one command execution (formalizes the legacy bash dict)."""

    identity: ExecutionIdentity
    terminal_state: ExecutionTerminalState
    exit_code: int
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    bounds: ExecutionBounds
    artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def _timed_out_consistency(self) -> "ExecutionCommandResult":
        # The timed_out flag and the TIMED_OUT terminal state must agree.
        if (
            self.timed_out
            and self.terminal_state is not ExecutionTerminalState.TIMED_OUT
        ):
            object.__setattr__(self, "terminal_state", ExecutionTerminalState.TIMED_OUT)
        return self


class ExecutionReadRequest(ContractModel):
    """A text-read request resolved to a backend-relative/validated path."""

    identity: ExecutionIdentity
    path: str = Field(min_length=1)
    max_bytes: int = Field(gt=0)


class ExecutionReadResult(ContractModel):
    """Typed result of a text read."""

    identity: ExecutionIdentity
    path: str
    content: str
    size_bytes: int = Field(ge=0)


class ExecutionWriteRequest(ContractModel):
    """A text-write request resolved to a backend-relative/validated path."""

    identity: ExecutionIdentity
    path: str = Field(min_length=1)
    content: str


class ExecutionWriteResult(ContractModel):
    """Typed result of a text write."""

    identity: ExecutionIdentity
    path: str
    bytes_written: int = Field(ge=0)


class CapabilitySnapshot(ContractModel):
    """RESERVED for EPIC-02. Minimal versioned backend facts. Defined now so the
    wire vocabulary is stable; the ``capabilities()`` method lands in EPIC-02."""

    schema_version: str = EXECUTION_SCHEMA_VERSION
    backend_id: str = Field(min_length=1)
    command: CapabilityState = CapabilityState.UNKNOWN
    file_read: CapabilityState = CapabilityState.UNKNOWN
    file_write: CapabilityState = CapabilityState.UNKNOWN
    reconnect: CapabilityState = CapabilityState.UNKNOWN
    teardown: CapabilityState = CapabilityState.UNKNOWN
    observed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_bounded_json_metadata(value, field_name="metadata")


__all__ = [
    "EXECUTION_SCHEMA_VERSION",
    "ExecutionTerminalState",
    "CapabilityState",
    "ExecutionIdentity",
    "ExecutionBounds",
    "ArtifactRef",
    "ExecutionCommandRequest",
    "ExecutionCommandResult",
    "ExecutionReadRequest",
    "ExecutionReadResult",
    "ExecutionWriteRequest",
    "ExecutionWriteResult",
    "CapabilitySnapshot",
]
