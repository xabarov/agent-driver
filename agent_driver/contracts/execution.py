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


EXECUTION_CAPABILITY_SCHEMA_VERSION = "agent_driver.execution.capability.v1"

# Bounds for the redaction-safe, deterministic capability surface.
_MAX_REASON_CHARS = 200
_MAX_PROGRAMS = 128
_MAX_LIMITATIONS = 32
_MAX_LIMITATION_CHARS = 200
# Substrings that make a metadata KEY look secret-bearing; such keys are rejected
# from a snapshot before it can reach persistence, events, logs, or a prompt.
_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "access_key",
    "auth",
)


def _reject_secret_like_keys(value: dict[str, Any], *, field_name: str) -> None:
    for key in value:
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            raise ValueError(
                f"{field_name} key {key!r} looks secret-bearing; a capability "
                "snapshot must not carry credentials"
            )


class CapabilityName(str, Enum):
    """The observable execution capabilities a snapshot reports on. A tool may
    require any of these; the host/backend observes them (never the model)."""

    COMMAND = "command"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    EVENT = "event"
    CONTROL = "control"
    ARTIFACT = "artifact"
    RECONNECT = "reconnect"
    TIMEOUT = "timeout"
    OUTPUT = "output"
    RESOURCE = "resource"
    TEARDOWN = "teardown"


class CapabilityStatus(ContractModel):
    """One capability's observed state plus an optional bounded reason.

    ``state`` is an observation, never a self-asserted claim. Missing evidence
    is :attr:`CapabilityState.UNKNOWN`, never ``SUPPORTED``.
    """

    state: CapabilityState = CapabilityState.UNKNOWN
    reason: str | None = Field(default=None, max_length=_MAX_REASON_CHARS)


class ProgramInfo(ContractModel):
    """A verified program/runtime the environment provides. Name (and version
    where verified) only — never the full PATH or environment dump."""

    name: str = Field(min_length=1)
    version: str | None = None


class ExecutionCapabilitySnapshot(ContractModel):
    """Truthful, revisioned facts about one prepared execution environment.

    Bound to a backend + environment revision (and a lease generation when one
    exists) so a cache entry can be keyed and its freshness reasoned about. The
    per-capability map defaults to ``UNKNOWN`` for any capability not reported —
    absence of evidence is never a support claim.
    """

    schema_version: str = EXECUTION_CAPABILITY_SCHEMA_VERSION
    backend_id: str = Field(min_length=1)
    environment_revision: str = Field(min_length=1)
    lease_generation: str | None = None
    observed_at: str | None = None
    digest: str | None = None
    capabilities: dict[CapabilityName, CapabilityStatus] = Field(default_factory=dict)
    programs: tuple[ProgramInfo, ...] = ()
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("programs")
    @classmethod
    def _bounded_programs(
        cls, value: tuple[ProgramInfo, ...]
    ) -> tuple[ProgramInfo, ...]:
        if len(value) > _MAX_PROGRAMS:
            raise ValueError(f"too many programs (>{_MAX_PROGRAMS})")
        return value

    @field_validator("limitations")
    @classmethod
    def _bounded_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > _MAX_LIMITATIONS:
            raise ValueError(f"too many limitations (>{_MAX_LIMITATIONS})")
        for item in value:
            if len(item) > _MAX_LIMITATION_CHARS:
                raise ValueError("limitation exceeds max length")
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_secret_like_keys(value, field_name="metadata")
        return ensure_bounded_json_metadata(value, field_name="metadata")

    def status_of(self, name: CapabilityName) -> CapabilityStatus:
        """Return the observed status of ``name``; ``UNKNOWN`` if unreported."""
        # pylint: disable=no-member  # pydantic dict field; .get is valid at runtime
        return self.capabilities.get(name, CapabilityStatus())

    def cache_key(self) -> str:
        """Stable identity for caching: backend + environment + lease generation."""
        return f"{self.backend_id}|{self.environment_revision}|{self.lease_generation or '-'}"


class ToolExecutionRequirement(ContractModel):
    """A tool's declared execution requirement — host/registry data, never a
    model-visible argument. A ``hard`` requirement withholds the tool pre-model
    and denies it pre-dispatch unless every named capability is ``SUPPORTED``.
    """

    required: tuple[CapabilityName, ...] = ()
    hard: bool = True

    @field_validator("required")
    @classmethod
    def _dedupe_nonempty(
        cls, value: tuple[CapabilityName, ...]
    ) -> tuple[CapabilityName, ...]:
        # order-preserving dedupe so the requirement is canonical
        seen: dict[CapabilityName, None] = {}
        for cap in value:
            seen.setdefault(cap, None)
        return tuple(seen)


class RequirementCheck(ContractModel):
    """Typed outcome of checking a tool requirement against a snapshot. The
    ``reason`` is host-facing and safe for events/logs (no secret values)."""

    satisfied: bool
    reason: str | None = Field(default=None, max_length=_MAX_REASON_CHARS)
    unmet: tuple[CapabilityName, ...] = ()


class EnvironmentBrief(ContractModel):
    """Deterministic, bounded, redaction-safe projection of a snapshot for
    request-only model context. Guidance, not an authorization boundary."""

    backend_id: str = Field(min_length=1)
    capability_revision: str = Field(min_length=1)
    supported: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    truncated: bool = False


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
    # EPIC-02 capabilities & routing
    "EXECUTION_CAPABILITY_SCHEMA_VERSION",
    "CapabilityName",
    "CapabilityStatus",
    "ProgramInfo",
    "ExecutionCapabilitySnapshot",
    "ToolExecutionRequirement",
    "RequirementCheck",
    "EnvironmentBrief",
]
