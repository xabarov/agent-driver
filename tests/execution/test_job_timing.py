"""EPIC-04 WP-E — job stage timings + failure-injection (timeouts, transport loss)."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution import ExecutionIdentity
from agent_driver.contracts.execution_job import (
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventKind,
    ExecutionEventPage,
    ExecutionHandle,
    ExecutionJobState,
    ExecutionReasonCode,
    ExecutionTerminalSnapshot,
)


def _cmd():
    return ex.ExecutionCommandRequest(
        identity=ExecutionIdentity(
            backend_id="fake",
            run_id="r",
            attempt_id="a",
            tool_call_id="t",
            request_id="req1",
        ),
        command="x",
        cwd="/w",
        timeout_seconds=30,
        max_output_chars=4000,
    )


def _handle():
    return ExecutionHandle(
        job_id="job-req1",
        idempotency_key="req1",
        backend_id="fake",
        execution_generation="gen-1",
    )


def _ev(seq, text, *, terminal=False):
    return ExecutionEvent(
        execution_generation="gen-1",
        sequence=seq,
        kind=ExecutionEventKind.OUTPUT,
        text=text,
        terminal=terminal,
    )


def _cur(seq):
    return ExecutionEventCursor(
        job_id="job-req1", execution_generation="gen-1", last_sequence=seq
    )


def _snapshot(state=ExecutionJobState.COMPLETED, *, reason_code=None):
    return ExecutionTerminalSnapshot(
        handle=_handle(), state=state, reason_code=reason_code
    )


@pytest.mark.asyncio
async def test_stage_timings_recorded_in_order():
    backend = ex.FakeExecutionBackend(
        job_pages=[
            ExecutionEventPage(
                events=(_ev(0, "a"), _ev(1, "b", terminal=True)),
                next_cursor=_cur(1),
                complete=True,
            )
        ],
        job_terminal=_snapshot(),
    )
    session = ex.JobSession(backend)
    await session.observe_to_terminal(await session.start(_cmd()))
    phases = [t.phase for t in session.timings]
    assert phases == ["start", "first_output", "terminal"]
    assert all(t.duration_ms >= 0 for t in session.timings)


@pytest.mark.parametrize(
    "reason",
    [
        ExecutionReasonCode.QUEUE_TIMEOUT,
        ExecutionReasonCode.IDLE_TIMEOUT,
        ExecutionReasonCode.EXECUTION_TIMEOUT,
        ExecutionReasonCode.CONTROL_TIMEOUT,
        ExecutionReasonCode.TEARDOWN_TIMEOUT,
    ],
)
@pytest.mark.asyncio
async def test_distinct_timeout_reason_codes_surface_on_terminal_timing(reason):
    backend = ex.FakeExecutionBackend(
        job_terminal=_snapshot(ExecutionJobState.TIMED_OUT, reason_code=reason)
    )
    session = ex.JobSession(backend)
    snap = await session.observe_to_terminal(await session.start(_cmd()))
    assert snap.reason_code is reason  # distinct, typed reason code
    terminal_timing = next(t for t in session.timings if t.phase == "terminal")
    assert terminal_timing.reason_code is reason


@pytest.mark.asyncio
async def test_transport_loss_during_observe_resolves_via_snapshot():
    class _LossOnObserve(ex.FakeExecutionBackend):
        async def observe(self, handle, cursor):
            raise ex.ExecutionTransportError("connection dropped")

    backend = _LossOnObserve(
        job_terminal=_snapshot(
            ExecutionJobState.INDETERMINATE,
            reason_code=ExecutionReasonCode.TRANSPORT_LOST,
        )
    )
    session = ex.JobSession(backend)
    # must NOT crash; resolves the outcome from the snapshot
    snap = await session.observe_to_terminal(await session.start(_cmd()))
    assert snap.state is ExecutionJobState.INDETERMINATE
    observe_timing = next(t for t in session.timings if t.phase == "observe")
    assert observe_timing.reason_code is ExecutionReasonCode.TRANSPORT_LOST


@pytest.mark.asyncio
async def test_indeterminate_start_records_reason():
    class _NoLookup(ex.FakeExecutionBackend):
        async def lookup_job(self, key):
            return None

    session = ex.JobSession(_NoLookup(lose_start=True))
    with pytest.raises(ex.IndeterminateExecutionError):
        await session.start(_cmd())
    start_timing = next(t for t in session.timings if t.phase == "start")
    assert start_timing.reason_code is ExecutionReasonCode.INDETERMINATE_DISPATCH
