"""Defensive default step-cap backstop on the agent loop.

A run whose model/executor never reaches a final answer would otherwise loop
forever, because AgentRunInput.max_steps/max_tool_calls/deadline all default to
None. RunnerConfig.default_max_steps applies a config-level backstop when the
run's own max_steps is None; an explicit per-run max_steps still wins, and
default_max_steps=None opts back into a fully unbounded loop.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentProfile, AgentRunInput
from agent_driver.contracts.enums import RunStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


class _RaisingCodeExecutor:
    """Executor that always raises — the run can never reach final_answer."""

    async def execute(self, **_kwargs):  # noqa: ANN003 - test stub
        raise RuntimeError("never finishes")


def _runner(**config_kwargs) -> FakeSingleStepRunner:
    # Disable the budget-grace synthesis window so these tests isolate the pure
    # cap/backstop terminal behaviour (grace is covered in test_budget_grace).
    config_kwargs.setdefault("budget_grace_enabled", False)
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            code_executor=_RaisingCodeExecutor(), **config_kwargs
        ),
    )


def _run_input(run_id: str, **kwargs) -> AgentRunInput:
    return AgentRunInput(
        input="loop forever",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        agent_profile=AgentProfile.CODE_AGENT,
        tool_policy={"metadata": {"code_action": "final_answer(2 + 2)"}},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_default_backstop_terminates_unbounded_run() -> None:
    """No per-run max_steps → the config backstop ends the run (no infinite loop)."""
    runner = _runner(default_max_steps=3)
    output = await runner.run(_run_input("run_backstop"))
    assert output.status == RunStatus.FAILED
    assert output.terminal_reason is not None
    assert output.terminal_reason.value == "max_steps_exceeded"
    # The loop is bounded near the backstop, not runaway (3 LLM steps interleave
    # with their tool steps, so total step_count is a small multiple of 3).
    assert output.metadata["step_count"] <= 3 * 4


@pytest.mark.asyncio
async def test_per_run_max_steps_overrides_backstop() -> None:
    """An explicit per-run max_steps wins over a larger config backstop."""
    runner = _runner(default_max_steps=50)
    output = await runner.run(_run_input("run_per_run_cap", max_steps=2))
    assert output.status == RunStatus.FAILED
    assert output.terminal_reason is not None
    assert output.terminal_reason.value == "max_steps_exceeded"
    assert output.metadata["step_count"] <= 2 * 4


@pytest.mark.asyncio
async def test_backstop_none_is_unbounded_but_per_run_cap_still_applies() -> None:
    """default_max_steps=None opts out of the backstop; per-run cap still bounds it."""
    runner = _runner(default_max_steps=None)
    # Without ANY cap this would loop forever, so assert the per-run cap is what
    # stops it — proving the backstop was genuinely disabled.
    output = await runner.run(_run_input("run_unbounded_optout", max_steps=2))
    assert output.status == RunStatus.FAILED
    assert output.terminal_reason is not None
    assert output.terminal_reason.value == "max_steps_exceeded"
    assert output.metadata["step_count"] <= 2 * 4
