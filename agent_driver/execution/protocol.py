"""The public ``ExecutionBackend`` protocol (EPIC-01 minimal surface).

A host-selected adapter that performs already-authorized command and text-file
operations in a prepared environment. EPIC-01 ships the smallest coherent surface
— command + text read/write — that routes the built-in ``bash``/``read``/``write``
without changing default local behavior. Lease, capability, event, and control
methods are reserved for later epics (their contract vocabulary already exists in
``agent_driver.contracts.execution``).

Governance is ABOVE dispatch: a backend method is reached only from inside an
already policy-, approval-, and guardrail-cleared tool handler. The backend does
not plan, select tools, or replace Agent Driver governance.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_driver.contracts.execution import (
    ExecutionCapabilitySnapshot,
    ExecutionCommandRequest,
    ExecutionCommandResult,
    ExecutionReadRequest,
    ExecutionReadResult,
    ExecutionWriteRequest,
    ExecutionWriteResult,
)
from agent_driver.contracts.execution_lease import (
    ExecutionLease,
    ExecutionLeaseRef,
    ExecutionLeaseRequest,
)


@runtime_checkable
class ExecutionBackend(Protocol):
    """Backend-neutral execution surface. Implementations must be safe to reuse
    across many tool calls and ReAct steps within one run."""

    @property
    def backend_id(self) -> str:
        """Stable identifier of this backend implementation/route."""

    async def run_command(
        self, request: ExecutionCommandRequest
    ) -> ExecutionCommandResult:
        """Run an already-authorized, bounded command and return a typed result.

        Must honor ``request.timeout_seconds`` and report ``timed_out`` rather
        than raising for a normal timeout. Raise a typed
        :class:`agent_driver.execution.errors.ExecutionError` for backend faults.
        """

    async def read_text(self, request: ExecutionReadRequest) -> ExecutionReadResult:
        """Return the text content at an already-resolved path."""

    async def write_text(self, request: ExecutionWriteRequest) -> ExecutionWriteResult:
        """Write text to an already-resolved path."""


@runtime_checkable
class CapabilityAwareBackend(ExecutionBackend, Protocol):
    """An ``ExecutionBackend`` that can also report a truthful capability
    snapshot (EPIC-02). Optional: a backend that does not implement
    ``capabilities`` is treated as all-``UNKNOWN`` (hard requirements then fail
    closed). Host-observed facts only — never model-asserted content."""

    async def capabilities(self) -> ExecutionCapabilitySnapshot:
        """Return the current capability snapshot for this backend/environment."""


@runtime_checkable
class LeaseCapableBackend(ExecutionBackend, Protocol):
    """An ``ExecutionBackend`` that grants task-scoped workspace leases (EPIC-03).

    Optional: a backend without these methods simply cannot be leased (the run
    uses it statelessly as in EPIC-01/02). ``acquire_lease``/``attach_lease``
    return a READY lease; ``release_lease`` destroys a runtime-owned environment;
    ``detach_lease`` relinquishes a host-owned one WITHOUT destroying it. All are
    idempotent on a stale/unknown reference.
    """

    async def acquire_lease(self, request: ExecutionLeaseRequest) -> ExecutionLease:
        """Acquire (or, when ``request.attach_ref`` is set, attach) a lease."""

    async def attach_lease(self, ref: ExecutionLeaseRef) -> ExecutionLease:
        """Re-attach to an existing lease by its safe reference (e.g. on resume);
        verify its generation is current or return a non-usable/EXPIRED lease."""

    async def release_lease(self, ref: ExecutionLeaseRef) -> None:
        """Destroy a RUNTIME-owned environment. Idempotent."""

    async def detach_lease(self, ref: ExecutionLeaseRef) -> None:
        """Relinquish a HOST-owned lease without destroying it. Idempotent."""


__all__ = ["ExecutionBackend", "CapabilityAwareBackend", "LeaseCapableBackend"]
