"""F3 (epic 051) — monotonic checkpoint revision + expected-revision guard."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.runtime.checkpoint_factory import (
    CheckpointChain,
    CheckpointSeed,
    build_checkpoint_ref,
)
from agent_driver.runtime.errors import ResumeConflictError
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


class _Row:
    def __init__(self, ref):
        self.ref = ref


def test_revision_is_monotonic_along_chain() -> None:
    seed = CheckpointSeed(
        run_id="r",
        attempt_id="a",
        thread_id=None,
        graph_id="g",
        node_id=None,
        storage_backend="mem",
        prior_checkpoint_id=None,
    )
    r0 = build_checkpoint_ref(seed=seed, chain=CheckpointChain(previous_row=None))
    r1 = build_checkpoint_ref(seed=seed, chain=CheckpointChain(previous_row=_Row(r0)))
    r2 = build_checkpoint_ref(seed=seed, chain=CheckpointChain(previous_row=_Row(r1)))
    assert [r0.revision, r1.revision, r2.revision] == [0, 1, 2]
    assert r2.parent_checkpoint_id == r1.checkpoint_id


def _runner():
    registry = ToolRegistry()
    calls: list[dict] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    store = InMemoryCheckpointStore()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=store,
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
        ),
    )
    return runner, calls, store


async def _pause(runner, run_id):
    paused = await runner.run(
        AgentRunInput(
            input="hi",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    return paused


@pytest.mark.asyncio
async def test_expected_revision_mismatch_conflicts() -> None:
    runner, calls, store = _runner()
    paused = await _pause(runner, "run_rev_stale")
    with pytest.raises(ResumeConflictError, match="expected revision"):
        await runner.run(
            AgentRunInput(
                run_id="run_rev_stale",
                resume=ResumeCommand(
                    interrupt_id=paused.interrupt.interrupt_id,
                    action=ResumeAction.APPROVE,
                    expected_revision=999,
                ),
                agent_id="agent",
                graph_preset="single_react",
                tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
            )
        )
    assert calls == []  # stale revision never ran the tool


@pytest.mark.asyncio
async def test_expected_revision_match_approves() -> None:
    runner, calls, store = _runner()
    paused = await _pause(runner, "run_rev_ok")
    pending = store.latest("run_rev_ok")
    assert pending is not None
    out = await runner.run(
        AgentRunInput(
            run_id="run_rev_ok",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                expected_revision=pending.ref.revision,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert out.status.value == "completed"
    assert len(calls) == 1
