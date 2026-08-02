"""U4 (epic 052) — abort observed during an in-flight LLM call cancels promptly."""

from __future__ import annotations

import asyncio
import time

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
    """A provider whose call takes far longer than the test is willing to wait."""

    async def complete(self, request):
        await asyncio.sleep(10)
        return await super().complete(request)


def _input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        max_steps=4,
    )


@pytest.mark.asyncio
async def test_abort_during_llm_call_cancels_promptly() -> None:
    # No wall-clock guard configured: the ONLY way this terminates before the
    # 10s provider sleep is the mid-LLM-await abort observation.
    runner = FakeSingleStepRunner(
        provider=_StuckProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(),
    )
    handle = RunAbortHandle()

    async def _abort_soon() -> None:
        await asyncio.sleep(0.05)  # after the first step-boundary check, mid-call
        handle.abort("operator stop")

    start = time.monotonic()
    run_task = asyncio.create_task(runner.run(_input("run_mid"), abort_handle=handle))
    await asyncio.gather(_abort_soon(), run_task)
    elapsed = time.monotonic() - start
    out = run_task.result()
    # Truthful terminal — a cancellation, not a MODEL_ERROR/DEADLINE_EXCEEDED.
    assert out.status == RunStatus.CANCELLED
    assert out.terminal_reason == TerminalReason.CANCELLED_BY_USER
    # Cancelled promptly (poll interval ~0.1s), not after the 10s provider sleep.
    assert elapsed < 3.0
