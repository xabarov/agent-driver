"""EPIC-04 WP-D — Stop/teardown: truthful, SEPARATE, capability-backed facts."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution_job import (
    ExecutionControlKind,
    ExecutionControlReceipt,
    ExecutionReasonCode,
)
from agent_driver.contracts.execution_lease import LeaseOwnership


def _handle():
    return ex.ExecutionHandle(
        job_id="j", idempotency_key="k", backend_id="fake", execution_generation="g1"
    )


@pytest.mark.asyncio
async def test_cooperative_only_backend_accepts_but_claims_nothing_more():
    # scenario 6: a cooperative-only backend accepts a STOP but does not claim
    # applied / execution-terminal / environment teardown.
    backend = ex.FakeExecutionBackend()  # default control: accepted, not applied
    outcome = await ex.stop_job(backend, _handle(), kind=ExecutionControlKind.STOP)
    assert outcome.accepted is True
    assert outcome.applied is False
    assert outcome.execution_terminal is False
    assert outcome.teardown_confirmed is False
    assert backend.job_teardowns == []  # no teardown attempted


@pytest.mark.asyncio
async def test_hard_teardown_backend_records_separate_confirmed_facts():
    # scenario 7: a backend that proves hard teardown reports applied +
    # execution-terminal + teardown-confirmed as SEPARATE receipts.
    backend = ex.FakeExecutionBackend(
        control_receipt=ExecutionControlReceipt(
            handle=_handle(),
            kind=ExecutionControlKind.STOP,
            accepted=True,
            applied=True,
            execution_terminal=True,
            reason_code=ExecutionReasonCode.OK,
        ),
        teardown_confirmed=True,
    )
    outcome = await ex.stop_job(
        backend,
        _handle(),
        ownership=LeaseOwnership.RUNTIME_OWNED,
        with_teardown=True,
    )
    assert outcome.accepted and outcome.applied and outcome.execution_terminal
    assert outcome.teardown_confirmed is True
    assert outcome.teardown is not None  # a distinct receipt


@pytest.mark.asyncio
async def test_host_owned_environment_is_never_torn_down():
    backend = ex.FakeExecutionBackend(teardown_confirmed=True)
    outcome = await ex.stop_job(
        backend,
        _handle(),
        ownership=LeaseOwnership.HOST_OWNED,
        with_teardown=True,
    )
    assert backend.job_teardowns == []  # host env never destroyed here
    assert outcome.teardown is None
    assert outcome.teardown_confirmed is False


@pytest.mark.asyncio
async def test_stop_without_teardown_request_does_not_teardown():
    backend = ex.FakeExecutionBackend(teardown_confirmed=True)
    outcome = await ex.stop_job(
        backend, _handle(), ownership=LeaseOwnership.RUNTIME_OWNED, with_teardown=False
    )
    assert backend.job_teardowns == []  # teardown is opt-in
    assert outcome.teardown is None


@pytest.mark.asyncio
async def test_control_request_carries_the_kind():
    backend = ex.FakeExecutionBackend()
    await ex.stop_job(backend, _handle(), kind=ExecutionControlKind.CANCEL_COOPERATIVE)
    assert backend.job_controls[0].kind is ExecutionControlKind.CANCEL_COOPERATIVE
