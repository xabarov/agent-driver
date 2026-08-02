"""F1 / U4 (epic 052) — attempt-epoch primitive, handler exposure, fencing guard."""

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
from agent_driver.runtime.single_agent.fencing import (
    RESERVED_ATTEMPT_EPOCH_KEY,
    attempt_epoch_of,
    is_stale_attempt,
    stamp_attempt_epoch,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import current_tool_attempt_epoch
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


# --------------------------------------------------------------------------- #
# Fencing guard helpers (pure)
# --------------------------------------------------------------------------- #


def test_stamp_only_when_epoch_positive() -> None:
    assert stamp_attempt_epoch({"a": 1}, 0) == {"a": 1}  # fresh run untouched
    stamped = stamp_attempt_epoch({"a": 1}, 2)
    assert stamped[RESERVED_ATTEMPT_EPOCH_KEY] == 2
    assert attempt_epoch_of(stamped) == 2
    assert attempt_epoch_of({"a": 1}) is None


def test_is_stale_attempt() -> None:
    assert is_stale_attempt(1, 2) is True  # older attempt → stale
    assert is_stale_attempt(2, 2) is False  # current
    assert is_stale_attempt(3, 2) is False  # future (shouldn't happen) not stale
    assert is_stale_attempt(None, 5) is False  # unstamped → treated current


# --------------------------------------------------------------------------- #
# Runtime: epoch increments on resume, and reaches the handler
# --------------------------------------------------------------------------- #


def _runner(seen: list[int]):
    registry = ToolRegistry()

    async def _danger(args):
        seen.append(current_tool_attempt_epoch())
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
        ),
    )


@pytest.mark.asyncio
async def test_resume_bumps_epoch_and_handler_sees_it() -> None:
    seen: list[int] = []
    runner = _runner(seen)
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_epoch",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    # A fresh run has not stamped an epoch (still 0).
    assert paused.metadata.get("attempt_epoch") in (None, 0)

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_epoch",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id, action=ResumeAction.APPROVE
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    # The resume re-drove the run: epoch bumped to 1 and the executed handler saw
    # it via current_tool_attempt_epoch() (the F1 attribution foundation).
    assert seen == [1]


@pytest.mark.asyncio
async def test_fresh_run_handler_sees_epoch_zero() -> None:
    seen: list[int] = []
    # A tool that runs on the first pass (no approval gate) sees epoch 0.
    registry = ToolRegistry()

    async def _lookup(args):
        seen.append(current_tool_attempt_epoch())
        return {"ok": True}

    from agent_driver.contracts import (
        ApprovalMode,
        SideEffectClass,
        ToolManifest,
        ToolRisk,
    )
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.contracts.tools import ToolCall
    from agent_driver.llm.contracts import LlmFinishReason, LlmResponse, UsageSummary

    registry.register(
        ToolManifest(
            name="lookup",
            description="read",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _lookup,
    )

    class _P(FakeProvider):
        def __init__(self):
            super().__init__(response_text="done")
            self.n = 0

        async def complete(self, request):
            self.n += 1
            u = UsageSummary(model_provider="fake", model_name="t")
            if self.n == 1:
                return LlmResponse(
                    message=ChatMessage(role="assistant", content=""),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    usage=u,
                    provider="fake",
                    model="t",
                    metadata={
                        "planned_tool_calls": [
                            ToolCall(tool_name="lookup", tool_call_id="c1", args={}).model_dump(
                                mode="json"
                            )
                        ]
                    },
                )
            return LlmResponse(
                message=ChatMessage(role="assistant", content="done"),
                finish_reason=LlmFinishReason.STOP,
                usage=u,
                provider="fake",
                model="t",
            )

    runner = FakeSingleStepRunner(
        provider=_P(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
        ),
    )
    out = await runner.run(
        AgentRunInput(
            input="hi",
            run_id="run_fresh",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert out.status.value == "completed"
    assert seen == [0]
