"""EPIC-03 WP-E — lease cleanup on all paths, pause retention, subagent policy."""

import contextlib
from types import SimpleNamespace

import pytest

import agent_driver.execution as ex
from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.execution_lease import LeaseOwnership
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)
from agent_driver.llm.providers_impl.fake import FakeProvider


def _runner(backend, *, ownership=LeaseOwnership.RUNTIME_OWNED):
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="done"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            execution_backend=backend, execution_lease_ownership=ownership
        ),
    )


def _context(runner, run_id, metadata=None):
    ctx = runner._init_context(
        AgentRunInput(
            input="hi", run_id=run_id, agent_id="a", graph_preset="single_react"
        )
    )
    if metadata:
        ctx.metadata.update(metadata)
    return ctx


async def _acquire(runner, backend, ctx):
    stack = contextlib.ExitStack()
    with stack:
        terminal = await runner._setup_execution_lease(ctx, backend, stack)
        return terminal


# --------------------------------------------------------------------------- #
# pause retention
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_paused_run_retains_lease():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend)
    ctx = _context(runner, "pause1")
    await _acquire(runner, backend, ctx)
    assert len(backend.lease_acquires) == 1

    await runner._release_execution_lease(ctx, SimpleNamespace(status=RunStatus.PAUSED))

    assert backend.lease_releases == []  # retained across the interrupt
    assert "execution_lease_ref" in ctx.metadata  # ref persisted for resume


@pytest.mark.asyncio
async def test_terminal_run_releases_and_records_receipts():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend)
    ctx = _context(runner, "term1")
    await _acquire(runner, backend, ctx)

    await runner._release_execution_lease(
        ctx, SimpleNamespace(status=RunStatus.COMPLETED)
    )

    assert len(backend.lease_releases) == 1
    receipts = ctx.metadata.get("execution_lease_receipts")
    assert receipts and any(r["phase"] == "release" for r in receipts)
    assert all("duration_ms" in r for r in receipts)  # timings observable


@pytest.mark.asyncio
async def test_release_is_idempotent_and_none_safe():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend)
    ctx = _context(runner, "idem1")
    await _acquire(runner, backend, ctx)
    await runner._release_execution_lease(ctx, SimpleNamespace(status=RunStatus.FAILED))
    await runner._release_execution_lease(ctx, None)  # None output, second call
    assert len(backend.lease_releases) == 1


# --------------------------------------------------------------------------- #
# subagent lease policy (ISOLATE default)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_subagent_child_does_not_acquire_lease():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend)  # ownership configured
    # a child run is identified by parent-handoff metadata
    ctx = _context(runner, "child1", metadata={"parent_run_id": "parent1"})
    terminal = await _acquire(runner, backend, ctx)
    assert terminal is None
    assert backend.lease_acquires == []  # isolate: child never touches a lease
    assert ctx.execution_lease_manager is None


@pytest.mark.asyncio
async def test_top_level_run_still_acquires():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend)
    ctx = _context(runner, "top1")  # no parent metadata
    await _acquire(runner, backend, ctx)
    assert len(backend.lease_acquires) == 1
    assert ctx.execution_lease_manager is not None


# --------------------------------------------------------------------------- #
# host-owned pause: retained, not detached
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_host_owned_paused_run_is_not_detached():
    backend = ex.FakeExecutionBackend()
    backend.known_generations["hostlease"] = "g1"
    ref = ex.ExecutionLeaseRef(
        lease_id="hostlease",
        generation="g1",
        backend_id="fake",
        ownership=LeaseOwnership.HOST_OWNED,
    )
    runner = _runner(backend, ownership=None)
    ctx = _context(
        runner,
        "hostpause",
        metadata={"execution_lease_ref": ref.model_dump(mode="json")},
    )
    await _acquire(runner, backend, ctx)

    await runner._release_execution_lease(ctx, SimpleNamespace(status=RunStatus.PAUSED))

    assert backend.lease_detaches == []  # host lease retained across pause
