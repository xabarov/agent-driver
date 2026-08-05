"""EPIC-04 Work Package A — execution job contracts + fencing observation."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution import ExecutionIdentity
from agent_driver.contracts.execution_job import (
    ExecutionControlKind,
    ExecutionControlReceipt,
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventKind,
    ExecutionEventPage,
    ExecutionHandle,
    ExecutionJobState,
    ExecutionReasonCode,
    ExecutionTerminalSnapshot,
)


def _handle(gen="gen-1", job="j1"):
    return ExecutionHandle(
        job_id=job, idempotency_key="k1", backend_id="fake", execution_generation=gen
    )


def _ev(gen, seq, text, *, terminal=False):
    return ExecutionEvent(
        execution_generation=gen,
        sequence=seq,
        kind=ExecutionEventKind.OUTPUT,
        text=text,
        terminal=terminal,
    )


def _cur(job="j1", gen="gen-1", seq=-1):
    return ExecutionEventCursor(job_id=job, execution_generation=gen, last_sequence=seq)


def _identity(request_id="req1"):
    return ExecutionIdentity(
        backend_id="fake",
        run_id="r",
        attempt_id="a",
        tool_call_id="t",
        request_id=request_id,
    )


def _cmd(request_id="req1"):
    return ex.ExecutionCommandRequest(
        identity=_identity(request_id),
        command="sleep 5",
        cwd="/w",
        timeout_seconds=30,
        max_output_chars=4000,
    )


# --------------------------------------------------------------------------- #
# contracts
# --------------------------------------------------------------------------- #
def test_handle_fences_stale_generation():
    assert _handle("gen-1").fences(_handle("gen-2")) is True
    assert _handle("gen-1").fences(_handle("gen-1")) is False


def test_event_identity_and_conflict():
    a = _ev("gen-1", 0, "x")
    dup = _ev("gen-1", 0, "x")
    conflict = _ev("gen-1", 0, "DIFFERENT")
    assert a.identity_key() == ("gen-1", 0)
    assert a.conflicts_with(dup) is False
    assert a.conflicts_with(conflict) is True


def test_event_metadata_rejects_secrets():
    with pytest.raises(ValueError):
        ExecutionEvent(
            execution_generation="g",
            sequence=0,
            kind=ExecutionEventKind.OUTPUT,
            metadata={"api_key": "x"},
        )


def test_terminal_snapshot_roundtrips():
    snap = ExecutionTerminalSnapshot(
        handle=_handle(), state=ExecutionJobState.COMPLETED, exit_code=0
    )
    assert ExecutionTerminalSnapshot.model_validate_json(snap.model_dump_json()) == snap


# --------------------------------------------------------------------------- #
# JobObserver — dedupe, fence, gap
# --------------------------------------------------------------------------- #
def test_observer_dedupes_and_fences_stale_generation():
    obs = ex.JobObserver(_handle("gen-1"))
    page = ExecutionEventPage(
        events=(
            _ev("gen-1", 0, "a"),
            _ev("gen-1", 1, "b"),
            _ev("gen-1", 0, "a"),  # duplicate
            _ev("OLD", 2, "stale"),  # stale generation
        ),
        next_cursor=_cur(seq=1),
    )
    fresh = obs.ingest(page)
    assert [e.text for e in fresh] == ["a", "b"]


def test_observer_gap_requests_snapshot():
    obs = ex.JobObserver(_handle())
    obs.ingest(
        ExecutionEventPage(events=(), next_cursor=_cur(seq=9), gap_detected=True)
    )
    assert obs.needs_snapshot is True


def test_observer_terminal_event_marks_complete():
    obs = ex.JobObserver(_handle())
    obs.ingest(
        ExecutionEventPage(
            events=(_ev("gen-1", 0, "done", terminal=True),),
            next_cursor=_cur(seq=0),
            complete=True,
        )
    )
    assert obs.complete is True


def test_observer_terminal_duplicate_idempotent_conflict_raises():
    obs = ex.JobObserver(_handle("gen-1"))
    snap = ExecutionTerminalSnapshot(
        handle=_handle("gen-1"), state=ExecutionJobState.COMPLETED, exit_code=0
    )
    obs.resolve_terminal(snap)
    obs.resolve_terminal(snap)  # duplicate is idempotent
    conflict = ExecutionTerminalSnapshot(
        handle=_handle("gen-1"), state=ExecutionJobState.FAILED, exit_code=1
    )
    with pytest.raises(ex.TerminalConflictError):
        obs.resolve_terminal(conflict)


def test_observer_fences_stale_terminal():
    obs = ex.JobObserver(_handle("gen-2"))
    stale = ExecutionTerminalSnapshot(
        handle=_handle("gen-1"), state=ExecutionJobState.COMPLETED
    )
    # stale-generation terminal is fenced (ignored), not a conflict
    obs.resolve_terminal(stale)
    assert obs.complete is False


# --------------------------------------------------------------------------- #
# Fake job backend + protocol
# --------------------------------------------------------------------------- #
def test_backend_is_job_capable():
    assert isinstance(ex.FakeExecutionBackend(), ex.JobCapableBackend)


@pytest.mark.asyncio
async def test_start_job_is_idempotent_by_request_id():
    fake = ex.FakeExecutionBackend()
    h1 = await fake.start_job(_cmd("req1"))
    h2 = await fake.start_job(_cmd("req1"))
    assert h1.job_id == h2.job_id  # same idempotency key -> same job


@pytest.mark.asyncio
async def test_lost_start_resolved_by_lookup():
    fake = ex.FakeExecutionBackend(lose_start=True)
    with pytest.raises(ex.ExecutionTimeoutError):
        await fake.start_job(_cmd("req1"))
    found = await fake.lookup_job("req1")
    assert found is not None and found.idempotency_key == "req1"


@pytest.mark.asyncio
async def test_snapshot_indeterminate_when_no_terminal_scripted():
    fake = ex.FakeExecutionBackend()
    handle = await fake.start_job(_cmd())
    snap = await fake.snapshot(handle)
    assert snap.state is ExecutionJobState.INDETERMINATE and snap.indeterminate


@pytest.mark.asyncio
async def test_control_reports_accepted_and_applied_separately():
    fake = ex.FakeExecutionBackend(
        control_receipt=ExecutionControlReceipt(
            handle=_handle(),
            kind=ExecutionControlKind.STOP,
            accepted=True,
            applied=False,
            reason_code=ExecutionReasonCode.UNSUPPORTED,
        )
    )
    receipt = await fake.control(
        ex.ExecutionControlRequest(handle=_handle(), kind=ExecutionControlKind.STOP)
    )
    assert receipt.accepted is True and receipt.applied is False


@pytest.mark.asyncio
async def test_teardown_confirmed_is_separate_fact():
    unconfirmed = ex.FakeExecutionBackend()
    r1 = await unconfirmed.teardown(_handle())
    assert r1.requested is True and r1.confirmed is False

    confirmed = ex.FakeExecutionBackend(teardown_confirmed=True)
    r2 = await confirmed.teardown(_handle())
    assert r2.confirmed is True and r2.reason_code is ExecutionReasonCode.OK
