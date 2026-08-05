"""EPIC-03 Work Package A — execution lease contracts + manager lifecycle."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution_lease import (
    ExecutionLeaseRef,
    ExecutionLeaseRequest,
    LeaseLifecyclePhase,
    LeaseOwnership,
    LeaseState,
)


def _req(request_id="r1", ownership=LeaseOwnership.RUNTIME_OWNED, attach_ref=None):
    return ExecutionLeaseRequest(
        request_id=request_id,
        backend_id="fake",
        ownership=ownership,
        attach_ref=attach_ref,
    )


# --------------------------------------------------------------------------- #
# contracts
# --------------------------------------------------------------------------- #
def test_ref_fences_stale_generation():
    a = ExecutionLeaseRef(lease_id="L", generation="g1", backend_id="b")
    b = ExecutionLeaseRef(lease_id="L", generation="g2", backend_id="b")
    same = ExecutionLeaseRef(lease_id="L", generation="g1", backend_id="b")
    assert a.fences(b) is True
    assert a.fences(same) is False


def test_lease_ref_roundtrips_and_is_the_durable_reference():
    ref = ExecutionLeaseRef(
        lease_id="L", generation="g1", backend_id="b", workspace_id="w"
    )
    assert ExecutionLeaseRef.model_validate_json(ref.model_dump_json()) == ref


def test_lease_request_rejects_secret_metadata():
    with pytest.raises(ValueError):
        ExecutionLeaseRequest(
            request_id="r", backend_id="b", metadata={"password": "x"}
        )


# --------------------------------------------------------------------------- #
# manager: acquire + reuse
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_acquire_once_then_reuse_is_idempotent():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()

    lease1 = await mgr.acquire_or_attach(backend, _req("r1"))
    lease2 = await mgr.acquire_or_attach(backend, _req("r1"))  # same request id

    assert lease1 is lease2  # reused, not re-acquired
    assert len(backend.lease_acquires) == 1
    assert lease1.is_usable


@pytest.mark.asyncio
async def test_runtime_owned_close_releases_exactly_once():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    lease = await mgr.acquire_or_attach(
        backend, _req(ownership=LeaseOwnership.RUNTIME_OWNED)
    )

    await mgr.close(backend)
    await mgr.close(backend)  # idempotent second close

    assert len(backend.lease_releases) == 1
    assert backend.lease_releases[0].lease_id == lease.ref.lease_id
    assert backend.lease_detaches == []


@pytest.mark.asyncio
async def test_host_owned_close_detaches_never_releases():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    await mgr.acquire_or_attach(backend, _req(ownership=LeaseOwnership.HOST_OWNED))

    await mgr.close(backend)

    assert len(backend.lease_detaches) == 1  # detach only
    assert backend.lease_releases == []  # never destroy host state


@pytest.mark.asyncio
async def test_close_with_no_lease_is_noop():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    await mgr.close(backend)  # never acquired
    assert backend.lease_releases == [] and backend.lease_detaches == []


# --------------------------------------------------------------------------- #
# manager: fail-closed behavior
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_lease_backend_raises_unsupported():
    class Minimal:
        backend_id = "m"

        async def run_command(self, r): ...
        async def read_text(self, r): ...
        async def write_text(self, r): ...

    mgr = ex.ExecutionLeaseManager()
    with pytest.raises(ex.UnsupportedCapabilityError):
        await mgr.acquire_or_attach(Minimal(), _req())


@pytest.mark.asyncio
async def test_non_ready_lease_fails_closed():
    backend = ex.FakeExecutionBackend(acquire_state=LeaseState.EXPIRED)
    mgr = ex.ExecutionLeaseManager()
    with pytest.raises(ex.LeaseNotUsableError):
        await mgr.acquire_or_attach(backend, _req())
    assert mgr.lease is None  # nothing usable retained


@pytest.mark.asyncio
async def test_attach_by_ref_stale_generation_fails_closed():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    lease = await mgr.acquire_or_attach(backend, _req())
    stale = lease.ref.model_copy(update={"generation": "old-gen"})

    mgr2 = ex.ExecutionLeaseManager()
    with pytest.raises(ex.LeaseNotUsableError):
        await mgr2.attach_by_ref(backend, stale)


@pytest.mark.asyncio
async def test_attach_by_ref_current_generation_succeeds():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    lease = await mgr.acquire_or_attach(backend, _req())

    mgr2 = ex.ExecutionLeaseManager()
    reattached = await mgr2.attach_by_ref(backend, lease.ref)
    assert reattached.is_usable
    assert reattached.ref.lease_id == lease.ref.lease_id


# --------------------------------------------------------------------------- #
# manager: cleanup never crashes the run
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_close_swallows_backend_release_error():
    class _Raising(ex.FakeExecutionBackend):
        async def release_lease(self, ref):
            raise RuntimeError("backend teardown failed")

    backend = _Raising()
    mgr = ex.ExecutionLeaseManager()
    await mgr.acquire_or_attach(backend, _req())

    await mgr.close(backend)  # must NOT raise

    phases = [r.phase for r in mgr.receipts]
    assert LeaseLifecyclePhase.TEARDOWN_PENDING in phases


@pytest.mark.asyncio
async def test_receipts_record_acquire_and_release_phases():
    backend = ex.FakeExecutionBackend()
    mgr = ex.ExecutionLeaseManager()
    await mgr.acquire_or_attach(backend, _req())
    await mgr.close(backend)
    phases = {r.phase for r in mgr.receipts}
    assert LeaseLifecyclePhase.ACQUIRE in phases
    assert LeaseLifecyclePhase.RELEASE in phases
