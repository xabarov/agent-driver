"""Run-level wall-clock guards (epic 019 phase A)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.runner import _step_timeout_seconds
from agent_driver.sdk import create_agent


def _context(deadline: float | None = None, started_offset: float = 0.0):
    from time import monotonic

    return SimpleNamespace(
        run_input=SimpleNamespace(deadline_seconds=deadline),
        started_at=monotonic() - started_offset,
    )


def test_step_timeout_prefers_tightest_bound():
    timeout, kind = _step_timeout_seconds(
        _context(deadline=100.0), hard_max_seconds=1800.0, idle_timeout_seconds=300.0
    )
    assert kind == "run_deadline" and timeout is not None and timeout <= 100.0
    timeout, kind = _step_timeout_seconds(
        _context(deadline=None), hard_max_seconds=1800.0, idle_timeout_seconds=300.0
    )
    assert kind == "step_idle" and timeout == 300.0
    timeout, kind = _step_timeout_seconds(
        _context(deadline=None, started_offset=1700.0),
        hard_max_seconds=1800.0,
        idle_timeout_seconds=300.0,
    )
    assert kind == "hard_max" and timeout is not None and timeout <= 100.0
    timeout, kind = _step_timeout_seconds(
        _context(deadline=None), hard_max_seconds=None, idle_timeout_seconds=None
    )
    assert timeout is None and kind == ""


class _WedgedProvider(FakeProvider):
    """Provider whose completion hangs long enough to trip the idle guard."""

    async def complete(self, request):
        await asyncio.sleep(5.0)
        return await super().complete(request)


@pytest.mark.asyncio
async def test_idle_guard_cuts_wedged_step():
    provider = _WedgedProvider(response_text="never returned")
    agent = create_agent(provider=provider)
    agent._runner._config.default_idle_timeout_seconds = 0.05
    agent._runner._config.default_hard_max_seconds = None
    output = await agent.run(
        AgentRunInput(
            input="hang please",
            run_id="run_idle_guard",
            agent_id="agent",
            graph_preset="single_react",
            stream=False,
        )
    )
    assert output.status.value in ("timed_out", "failed")
    assert output.terminal_reason.value == "deadline_exceeded"
