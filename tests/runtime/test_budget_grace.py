"""Soft-budget grace: one forced-final synthesis window before hard-failing.

When a soft budget (max_steps / max_tool_calls / cost) is exhausted, the runtime
grants a bounded forced-final window (tools disabled) so the model can produce a
best-effort answer instead of returning a bare FAILED with an empty answer. The
window is bounded so a misbehaving model cannot reopen the runaway.

The authoritative quality check (does the grace answer actually pass
answer-relevance) is the live eval A/B; these tests pin the mechanism:
grace opens a *bounded extra* window and never loops forever.
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
    async def execute(self, **_kwargs):  # noqa: ANN003 - test stub
        raise RuntimeError("never finishes")


def _run(grace: bool, run_id: str):
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            code_executor=_RaisingCodeExecutor(),
            default_max_steps=3,
            budget_grace_enabled=grace,
        ),
    )
    return runner.run(
        AgentRunInput(
            input="loop",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "final_answer(2 + 2)"}},
        )
    )


@pytest.mark.asyncio
async def test_grace_opens_bounded_extra_window() -> None:
    """Grace ON runs strictly longer than OFF (the synthesis window opened),
    yet both terminate and stay bounded (no runaway reopened)."""
    off = await _run(False, "grace_off")
    on = await _run(True, "grace_on")

    # Both terminate deterministically — neither loops forever.
    assert off.status == RunStatus.FAILED
    assert on.status == RunStatus.FAILED

    steps_off = off.metadata["step_count"]
    steps_on = on.metadata["step_count"]
    # The grace window adds steps (forced-final attempts) on top of the cap...
    assert steps_on > steps_off
    # ...but stays bounded: the extra window is a few LLM steps, not unbounded.
    assert steps_on <= steps_off * 3


@pytest.mark.asyncio
async def test_grace_disabled_matches_hard_cap() -> None:
    """With grace disabled the run hard-fails exactly at the cap (no window)."""
    off = await _run(False, "grace_off_strict")
    assert off.status == RunStatus.FAILED
    assert off.terminal_reason is not None
    assert off.terminal_reason.value == "max_steps_exceeded"
