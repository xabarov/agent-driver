"""U4 A/D (epic 052) — runner records the durable abort lifecycle."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import RunStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    AbortLifecycleState,
    FakeSingleStepRunner,
    InMemoryAbortLifecycleStore,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunAbortHandle,
    RunnerConfig,
    SqliteAbortLifecycleStore,
)


def _runner(store):
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(abort_store=store),
    )


def _input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        max_steps=8,
    )


@pytest.mark.asyncio
async def test_aborted_run_records_observed_and_cancelled(tmp_path) -> None:
    path = str(tmp_path / "abort.db")
    store = SqliteAbortLifecycleStore(path=path)
    handle = RunAbortHandle()
    handle.abort("operator stop")
    out = await _runner(store).run(_input("run_ab_cancel"), abort_handle=handle)
    assert out.status == RunStatus.CANCELLED
    rec = store.get("run_ab_cancel")
    assert rec is not None
    assert rec.state is AbortLifecycleState.CANCELLED
    assert rec.observed is True  # the transition the old record never made
    # Durable: a fresh store instance (as after a restart) still sees it.
    reopened = SqliteAbortLifecycleStore(path=path)
    assert reopened.get("run_ab_cancel").state is AbortLifecycleState.CANCELLED


@pytest.mark.asyncio
async def test_completed_before_cancel_is_recorded(tmp_path) -> None:
    store = InMemoryAbortLifecycleStore()
    # A durable stop was requested (e.g. from another process) ...
    store.request_abort("run_cbc", reason="late stop")
    # ... but the run completes before the runner ever observes it.
    out = await _runner(store).run(_input("run_cbc"))
    assert out.status == RunStatus.COMPLETED
    rec = store.get("run_cbc")
    assert rec is not None
    assert rec.state is AbortLifecycleState.COMPLETED_BEFORE_CANCEL
    assert rec.observed is False


@pytest.mark.asyncio
async def test_normal_run_without_abort_leaves_no_record(tmp_path) -> None:
    store = InMemoryAbortLifecycleStore()
    out = await _runner(store).run(_input("run_clean"))
    assert out.status == RunStatus.COMPLETED
    assert store.get("run_clean") is None  # nothing to record
