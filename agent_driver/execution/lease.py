"""Run-scoped execution-lease manager (EPIC-03 Work Package A).

A small session component (NOT a tool, NOT a second agent loop) that owns the
correct use of one lease inside a run: acquire/attach once (idempotent per
request id), reuse across steps, and close exactly once on every exit —
releasing a runtime-owned environment or detaching a host-owned one. Cleanup is
idempotent so it is safe from a ``finally``-equivalent path on normal, error,
timeout, and cancellation exits.

The manager fails closed: it never silently substitutes local execution for a
lease it could not acquire, and it never destroys host-owned external state.
"""

from __future__ import annotations

from time import monotonic

from agent_driver.contracts.execution_lease import (
    ExecutionLease,
    ExecutionLeaseRef,
    ExecutionLeaseRequest,
    LeaseLifecyclePhase,
    LeaseOwnership,
    LeaseReceipt,
)
from agent_driver.execution.errors import (
    ExecutionError,
    UnsupportedCapabilityError,
)


class LeaseNotUsableError(ExecutionError):
    """A lease was acquired/attached but is not READY (expired/failed), so no
    work may run against it. Fails closed rather than falling back to local."""

    code = "lease_not_usable"


def _lease_capable(backend: object) -> bool:
    return callable(getattr(backend, "acquire_lease", None)) and callable(
        getattr(backend, "release_lease", None)
    )


class ExecutionLeaseManager:
    """Owns one lease for the lifetime of a run. Not thread-safe by design: it is
    driven by the single-agent loop within one run."""

    def __init__(self) -> None:
        self._lease: ExecutionLease | None = None
        self._request_id: str | None = None
        self._closed = False
        self._receipts: list[LeaseReceipt] = []

    @property
    def lease(self) -> ExecutionLease | None:
        """The current accepted lease, or ``None`` if none is held."""
        return self._lease

    @property
    def receipts(self) -> tuple[LeaseReceipt, ...]:
        return tuple(self._receipts)

    def _record(
        self,
        *,
        ref: ExecutionLeaseRef,
        phase: LeaseLifecyclePhase,
        outcome: str,
        started: float,
        reason_code: str | None = None,
    ) -> None:
        self._receipts.append(
            LeaseReceipt(
                lease_id=ref.lease_id,
                generation=ref.generation,
                phase=phase,
                ownership=ref.ownership,
                outcome=outcome,
                duration_ms=max(0.0, (monotonic() - started) * 1000.0),
                reason_code=reason_code,
            )
        )

    async def acquire_or_attach(
        self, backend: object, request: ExecutionLeaseRequest
    ) -> ExecutionLease:
        """Acquire or attach a lease, idempotently for one request id.

        Re-invoking with the same ``request_id`` returns the already-accepted
        lease (reuse across steps) instead of acquiring a second environment.
        Raises :class:`UnsupportedCapabilityError` when the backend cannot lease,
        and :class:`LeaseNotUsableError` when the backend returns a non-READY
        lease — never a silent local fallback.
        """
        if self._closed:
            raise LeaseNotUsableError("lease manager already closed")
        if self._lease is not None and self._request_id == request.request_id:
            return self._lease  # idempotent reuse
        if not _lease_capable(backend):
            raise UnsupportedCapabilityError(
                "backend does not support execution leases"
            )
        started = monotonic()
        phase = (
            LeaseLifecyclePhase.ACQUIRE
            if request.attach_ref is None
            else LeaseLifecyclePhase.READY
        )
        if request.attach_ref is not None:
            lease = await backend.attach_lease(request.attach_ref)  # type: ignore[attr-defined]
        else:
            lease = await backend.acquire_lease(request)  # type: ignore[attr-defined]
        if not isinstance(lease, ExecutionLease):
            raise LeaseNotUsableError("backend returned a non-lease result")
        self._record(
            ref=lease.ref,
            phase=phase,
            outcome=lease.state.value,
            started=started,
        )
        if not lease.is_usable:
            self._record(
                ref=lease.ref,
                phase=LeaseLifecyclePhase.READY,
                outcome="not_usable",
                started=started,
                reason_code=lease.state.value,
            )
            raise LeaseNotUsableError(
                f"lease {lease.ref.lease_id} is {lease.state.value}, not usable"
            )
        self._lease = lease
        self._request_id = request.request_id
        return lease

    async def attach_by_ref(
        self, backend: object, ref: ExecutionLeaseRef
    ) -> ExecutionLease:
        """Resume path: re-attach by safe reference and verify usability. A
        stale/expired lease raises :class:`LeaseNotUsableError` (fail closed)."""
        if not _lease_capable(backend) or not callable(
            getattr(backend, "attach_lease", None)
        ):
            raise UnsupportedCapabilityError("backend cannot attach a lease")
        started = monotonic()
        lease = await backend.attach_lease(ref)  # type: ignore[attr-defined]
        if not isinstance(lease, ExecutionLease) or not lease.is_usable:
            state = getattr(getattr(lease, "state", None), "value", "unknown")
            self._record(
                ref=ref,
                phase=LeaseLifecyclePhase.READY,
                outcome="attach_stale",
                started=started,
                reason_code=state,
            )
            raise LeaseNotUsableError(
                f"attach of lease {ref.lease_id} failed closed ({state})"
            )
        self._record(
            ref=lease.ref,
            phase=LeaseLifecyclePhase.READY,
            outcome=lease.state.value,
            started=started,
        )
        self._lease = lease
        return lease

    async def close(self, backend: object) -> None:
        """Release (runtime-owned) or detach (host-owned) exactly once.

        Idempotent and exception-swallowing: safe to call from a
        ``finally``-equivalent path on any exit. A backend error during release
        is recorded as a teardown-pending receipt, never re-raised into the run's
        terminal path.
        """
        if self._closed:
            return
        self._closed = True
        lease = self._lease
        if lease is None:
            return
        ref = lease.ref
        host_owned = ref.ownership is LeaseOwnership.HOST_OWNED
        phase = (
            LeaseLifecyclePhase.DETACH if host_owned else LeaseLifecyclePhase.RELEASE
        )
        started = monotonic()
        method = getattr(
            backend, "detach_lease" if host_owned else "release_lease", None
        )
        if not callable(method):
            self._record(
                ref=ref,
                phase=LeaseLifecyclePhase.TEARDOWN_PENDING,
                outcome="no_teardown_method",
                started=started,
            )
            return
        try:
            await method(ref)
            self._record(ref=ref, phase=phase, outcome="ok", started=started)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # cleanup must never crash a run
            self._record(
                ref=ref,
                phase=LeaseLifecyclePhase.TEARDOWN_PENDING,
                outcome="teardown_error",
                started=started,
                reason_code=type(exc).__name__,
            )


__all__ = ["ExecutionLeaseManager", "LeaseNotUsableError"]
