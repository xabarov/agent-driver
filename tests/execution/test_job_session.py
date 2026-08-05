"""EPIC-04 WP-C — job reconnect, fencing, lost-start, and recovery persistence."""

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
    ExecutionTerminalSnapshot,
)


def _cmd(request_id="req1"):
    return ex.ExecutionCommandRequest(
        identity=ExecutionIdentity(
            backend_id="fake",
            run_id="r",
            attempt_id="a",
            tool_call_id="t",
            request_id=request_id,
        ),
        command="tail -f log",
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


def _terminal(state=ExecutionJobState.COMPLETED, exit_code=0):
    return ExecutionTerminalSnapshot(handle=_handle(), state=state, exit_code=exit_code)


# --------------------------------------------------------------------------- #
# start: idempotency + lost-start recovery + indeterminate
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_start_returns_handle():
    handle = await ex.JobSession(ex.FakeExecutionBackend()).start(_cmd())
    assert handle.idempotency_key == "req1"


@pytest.mark.asyncio
async def test_lost_start_resolved_by_lookup():
    handle = await ex.JobSession(ex.FakeExecutionBackend(lose_start=True)).start(_cmd())
    assert handle.job_id == "job-req1"  # resolved, not re-dispatched


@pytest.mark.asyncio
async def test_unresolved_lost_start_is_indeterminate_not_rerun():
    class _NoLookup(ex.FakeExecutionBackend):
        async def lookup_job(self, key):
            return None

    with pytest.raises(ex.IndeterminateExecutionError):
        await ex.JobSession(_NoLookup(lose_start=True)).start(_cmd())


# --------------------------------------------------------------------------- #
# observe to terminal
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_observe_streams_events_then_terminal():
    backend = ex.FakeExecutionBackend(
        job_pages=[
            ExecutionEventPage(
                events=(_ev(0, "a"), _ev(1, "b", terminal=True)),
                next_cursor=_cur(1),
                complete=True,
            )
        ],
        job_terminal=_terminal(),
    )
    session = ex.JobSession(backend)
    handle = await session.start(_cmd())
    seen = []
    snap = await session.observe_to_terminal(
        handle, on_event=lambda e: seen.append(e.text)
    )
    assert seen == ["a", "b"]
    assert snap.state is ExecutionJobState.COMPLETED and snap.exit_code == 0


@pytest.mark.asyncio
async def test_gap_falls_back_to_snapshot():
    backend = ex.FakeExecutionBackend(
        job_pages=[
            ExecutionEventPage(events=(), next_cursor=_cur(9), gap_detected=True)
        ],
        job_terminal=_terminal(),
    )
    session = ex.JobSession(backend)
    snap = await session.observe_to_terminal(await session.start(_cmd()))
    assert snap.state is ExecutionJobState.COMPLETED  # resolved via snapshot


@pytest.mark.asyncio
async def test_duplicate_and_stale_events_are_fenced_in_observation():
    backend = ex.FakeExecutionBackend(
        job_pages=[
            ExecutionEventPage(
                events=(
                    _ev(0, "a"),
                    _ev(0, "a"),  # duplicate
                    ExecutionEvent(
                        execution_generation="OLD",
                        sequence=1,
                        kind=ExecutionEventKind.OUTPUT,
                        text="stale",
                    ),
                    _ev(1, "b", terminal=True),
                ),
                next_cursor=_cur(1),
                complete=True,
            )
        ],
        job_terminal=_terminal(),
    )
    session = ex.JobSession(backend)
    seen = []
    await session.observe_to_terminal(
        await session.start(_cmd()), on_event=lambda e: seen.append(e.text)
    )
    assert seen == ["a", "b"]  # duplicate + stale-generation dropped


# --------------------------------------------------------------------------- #
# recovery persistence (restart)
# --------------------------------------------------------------------------- #
def test_persist_and_restore_recovery_roundtrip():
    rec = ex.persist_job_recovery(_handle(), _cur(3))
    restored = ex.restore_job_recovery(rec)
    assert restored is not None
    handle, cursor = restored
    assert handle.job_id == "job-req1"
    assert cursor.last_sequence == 3


def test_restore_malformed_recovery_fails_closed():
    assert ex.restore_job_recovery({"handle": {"bad": 1}}) is None
    assert ex.restore_job_recovery("nonsense") is None


def test_recovery_reference_is_json_safe():
    import json

    rec = ex.persist_job_recovery(_handle(), _cur(3))
    # round-trips through JSON (checkpoint-safe, non-secret)
    assert ex.restore_job_recovery(json.loads(json.dumps(rec))) is not None
