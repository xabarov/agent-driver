"""Public validated contracts for execution leases (EPIC-03).

A task-scoped, generation-bound right to use ONE prepared workspace across the
whole agent loop. The external backend owns the infrastructure; Agent Driver
owns correct use of the lease inside the run: acquire/attach once, reuse across
steps, and detach (host-owned) or release (runtime-owned) on every exit.

Distinct from ``BackgroundRunLease`` (durable run lifecycle) — an execution lease
is about a workspace/environment, not a run's background continuation. Lease
references are NON-secret, generation-bound, and safe for durable checkpoint
state; credentials live only in backend/host configuration.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.execution import (
    ExecutionCapabilitySnapshot,
    _reject_secret_like_keys,
)
from agent_driver.contracts.validation import ensure_bounded_json_metadata

EXECUTION_LEASE_SCHEMA_VERSION = "agent_driver.execution.lease.v1"


class LeaseOwnership(str, Enum):
    """Who is responsible for destroying the underlying environment.

    ``RUNTIME_OWNED`` — Agent Driver acquired it and MUST release it on exit.
    ``HOST_OWNED`` — the host provisioned it and only ever gets a detach; Agent
    Driver must never destroy external state it did not create.
    """

    RUNTIME_OWNED = "runtime_owned"
    HOST_OWNED = "host_owned"


class LeaseState(str, Enum):
    """Lifecycle state of one execution lease. A stale generation is EXPIRED and
    fails closed — it can never receive new work."""

    PENDING = "pending"
    READY = "ready"
    EXPIRED = "expired"
    RELEASED = "released"
    DETACHED = "detached"
    FAILED = "failed"


class LeaseLifecyclePhase(str, Enum):
    """Independently-observable phases for lease timings/receipts."""

    QUEUE = "queue"
    ACQUIRE = "acquire"
    READY = "ready"
    DETACH = "detach"
    RELEASE = "release"
    TEARDOWN_PENDING = "teardown_pending"


class ExecutionLeaseRequest(ContractModel):
    """A request to acquire (or attach) a workspace lease.

    ``request_id`` is the idempotency key: the same request id must not acquire
    two environments. ``workspace_id`` is CORRELATION ONLY — it cannot attach a
    lease by itself. ``attach_ref`` attaches an existing host-owned lease.
    """

    request_id: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    workspace_id: str | None = None
    ownership: LeaseOwnership = LeaseOwnership.RUNTIME_OWNED
    attach_ref: "ExecutionLeaseRef | None" = None
    ttl_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_secret_like_keys(value, field_name="metadata")
        return ensure_bounded_json_metadata(value, field_name="metadata")


class ExecutionLeaseRef(ContractModel):
    """The safe, durable reference to a lease. Non-secret and generation-bound —
    this is ALL that is persisted to a checkpoint. A resume re-attaches by this
    reference and re-verifies generation/capabilities before new work; it must
    never assume a previous in-process lease object survived."""

    schema_version: str = EXECUTION_LEASE_SCHEMA_VERSION
    lease_id: str = Field(min_length=1)
    generation: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    ownership: LeaseOwnership = LeaseOwnership.RUNTIME_OWNED
    workspace_id: str | None = None

    def fences(self, other: "ExecutionLeaseRef") -> bool:
        """True when ``other`` is the same lease at a DIFFERENT (stale)
        generation — a late result/attach from ``other`` must be fenced."""
        return self.lease_id == other.lease_id and self.generation != other.generation


class WorkspacePaths(ContractModel):
    """Backend-relative path contract for a workspace. Local ``Path.resolve()``
    cannot validate remote state, so the backend declares its roots and the
    routing layer validates against THESE, not the local filesystem."""

    workspace_root: str = Field(min_length=1)
    writable_roots: tuple[str, ...] = ()
    allow_symlink_escape: bool = False


class ExecutionLease(ContractModel):
    """A live, accepted lease: its safe reference, state, expiry, the workspace
    path contract, and the capability snapshot observed for THIS generation."""

    ref: ExecutionLeaseRef
    state: LeaseState = LeaseState.PENDING
    expires_at: str | None = None
    paths: WorkspacePaths | None = None
    capabilities: ExecutionCapabilitySnapshot | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_secret_like_keys(value, field_name="metadata")
        return ensure_bounded_json_metadata(value, field_name="metadata")

    @property
    def is_usable(self) -> bool:
        """A lease may receive work only when READY (never PENDING/EXPIRED/…)."""
        return self.state is LeaseState.READY


class LeaseReceipt(ContractModel):
    """A typed, redaction-safe receipt for one lease lifecycle phase — timings
    and stable reason codes only, no secrets."""

    lease_id: str = Field(min_length=1)
    generation: str = Field(min_length=1)
    phase: LeaseLifecyclePhase
    ownership: LeaseOwnership
    outcome: str = Field(min_length=1)
    duration_ms: float | None = Field(default=None, ge=0)
    reason_code: str | None = None


__all__ = [
    "EXECUTION_LEASE_SCHEMA_VERSION",
    "LeaseOwnership",
    "LeaseState",
    "LeaseLifecyclePhase",
    "ExecutionLeaseRequest",
    "ExecutionLeaseRef",
    "WorkspacePaths",
    "ExecutionLease",
    "LeaseReceipt",
]
