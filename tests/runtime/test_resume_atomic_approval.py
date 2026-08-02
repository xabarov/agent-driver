"""U3 (epic 051) — expected-checkpoint + idempotent approval consumption.

Covers the contract + optimistic-concurrency + idempotent-replay slice:
- expected_checkpoint_id mismatch -> stable ResumeConflictError, tool NOT run;
- expected_checkpoint_id match -> approves and runs the tool exactly once;
- a duplicate approve of an already-consumed interrupt -> ResumeConflictError
  (no second tool execution);
- a duplicate carrying the same idempotency_key (even with a different
  interrupt id) is recognised as already-consumed.

Reuses the HITL pause/resume harness from ``test_tool_governance_hitl``.
"""

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
from agent_driver.runtime.errors import ResumeConflictError
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


def _runner_with_counter():
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    return runner, calls


async def _pause(runner, run_id: str):
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    return paused


def _resume_input(run_id: str, resume: ResumeCommand) -> AgentRunInput:
    return AgentRunInput(
        run_id=run_id,
        resume=resume,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


@pytest.mark.asyncio
async def test_expected_checkpoint_mismatch_conflicts() -> None:
    runner, calls = _runner_with_counter()
    paused = await _pause(runner, "run_u3_stale")
    with pytest.raises(ResumeConflictError, match="expected checkpoint"):
        await runner.run(
            _resume_input(
                "run_u3_stale",
                ResumeCommand(
                    interrupt_id=paused.interrupt.interrupt_id,
                    action=ResumeAction.APPROVE,
                    expected_checkpoint_id="chk_does_not_match",
                ),
            )
        )
    assert calls == []  # stale approval never ran the tool


@pytest.mark.asyncio
async def test_expected_checkpoint_match_approves_once() -> None:
    runner, calls = _runner_with_counter()
    paused = await _pause(runner, "run_u3_match")
    # The host learns the pending checkpoint id from the durable checkpoint
    # store (the interrupt itself still carries the sentinel "checkpoint_pending"
    # — binding it to the real id is the remaining U3 phase-C work).
    pending_ckpt = runner._deps.checkpoint_store.latest("run_u3_match")
    assert pending_ckpt is not None
    out = await runner.run(
        _resume_input(
            "run_u3_match",
            ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                expected_checkpoint_id=pending_ckpt.ref.checkpoint_id,
            ),
        )
    )
    assert out.status.value == "completed"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_duplicate_approve_after_consume_conflicts() -> None:
    runner, calls = _runner_with_counter()
    paused = await _pause(runner, "run_u3_dup")
    first = await runner.run(
        _resume_input(
            "run_u3_dup",
            ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
        )
    )
    assert first.status.value == "completed"
    assert len(calls) == 1
    # Replaying the same approval must not run the tool a second time.
    with pytest.raises(ResumeConflictError, match="already consumed"):
        await runner.run(
            _resume_input(
                "run_u3_dup",
                ResumeCommand(
                    interrupt_id=paused.interrupt.interrupt_id,
                    action=ResumeAction.APPROVE,
                ),
            )
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_conflicts() -> None:
    runner, calls = _runner_with_counter()
    paused = await _pause(runner, "run_u3_key")
    await runner.run(
        _resume_input(
            "run_u3_key",
            ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                idempotency_key="approve-key-1",
            ),
        )
    )
    assert len(calls) == 1
    # A retry carrying the same idempotency key — even with a different
    # interrupt id — is recognised as already consumed.
    with pytest.raises(ResumeConflictError, match="already consumed"):
        await runner.run(
            _resume_input(
                "run_u3_key",
                ResumeCommand(
                    interrupt_id="int_some_other_id",
                    action=ResumeAction.APPROVE,
                    idempotency_key="approve-key-1",
                ),
            )
        )
    assert len(calls) == 1
