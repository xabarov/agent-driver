"""U4 D (epic 052) — CANCELLATION_FAILED for an uncooperative stuck step."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunAbortHandle,
    RunnerConfig,
)


class _StuckProvider(FakeProvider):
    """A provider whose call blows any wall-clock guard (ignores cancellation)."""

    async def complete(self, request):
        await asyncio.sleep(5)
        return await super().complete(request)


def _runner():
    return FakeSingleStepRunner(
        provider=_StuckProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(default_idle_timeout_seconds=0.1),
    )


def _input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        max_steps=4,
    )


@pytest.mark.asyncio
async def test_abort_during_stuck_step_is_cancellation_failed() -> None:
    handle = RunAbortHandle()

    async def _abort_soon() -> None:
        await asyncio.sleep(0.02)  # after the first step-boundary check, mid-step
        handle.abort("operator stop")

    run_task = asyncio.create_task(_runner().run(_input("run_cf"), abort_handle=handle))
    await asyncio.gather(_abort_soon(), run_task)
    out = run_task.result()
    assert out.status == RunStatus.CANCELLED
    assert out.terminal_reason == TerminalReason.CANCELLATION_FAILED


@pytest.mark.asyncio
async def test_plain_timeout_without_abort_is_deadline_exceeded() -> None:
    # No abort in play → the stuck step is a plain deadline, not a failed cancel.
    out = await _runner().run(_input("run_dl"))
    assert out.status == RunStatus.TIMED_OUT
    assert out.terminal_reason == TerminalReason.DEADLINE_EXCEEDED
