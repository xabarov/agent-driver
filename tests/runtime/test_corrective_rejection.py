"""opencode-adoption EPIC-04 — correcting-rejection feedback.

When ``RunnerConfig.corrective_rejection_enabled`` is set, an operator REJECT that
carries a ``message`` on a (non-plan) tool-approval interrupt denies the pending call
but CONTINUES the run, folding the feedback into the next model turn as steering
(opencode's ``CorrectedError``). A bare REJECT — or the default (flag off) — still
aborts the run FAILED (opencode's ``RejectedError``). Pins:

* enabled + feedback → run completes (not failed), the tool never executed, and the
  resumed model turn carries the operator feedback + the rejected tool name;
* default (flag off) → REJECT terminates FAILED (unchanged);
* enabled but no message → REJECT terminates FAILED (bare rejection stays a hard stop).
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
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


class _RecordingProvider(FakeProvider):
    """FakeProvider that records every request's message content."""

    def __init__(self) -> None:
        super().__init__(response_text="all set")
        self.seen_messages: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.seen_messages.extend(m.content for m in request.messages)
        return await super().complete(request)


def _executed_calls_registry() -> tuple[ToolRegistry, list[dict]]:
    calls: list[dict] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args.get('target')}"}

    registry = ToolRegistry()
    registry.register(danger_tool_manifest(), _danger)
    return registry, calls


async def _pause_on_danger(runner: FakeSingleStepRunner, run_id: str):
    return await runner.run(
        AgentRunInput(
            input="hello",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )


def _make_runner(config: RunnerConfig, provider=None) -> FakeSingleStepRunner:
    return FakeSingleStepRunner(
        provider=provider or FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=config,
    )


@pytest.mark.asyncio
async def test_corrective_rejection_continues_with_feedback() -> None:
    registry, calls = _executed_calls_registry()
    provider = _RecordingProvider()
    runner = _make_runner(
        RunnerConfig(
            corrective_rejection_enabled=True,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
        provider=provider,
    )
    paused = await _pause_on_danger(runner, "run_corrective")
    assert paused.status.value == "paused"
    assert paused.interrupt is not None

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_corrective",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
                message="Do not touch production; use the staging table instead.",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    # Run continued to a normal completion rather than terminating FAILED.
    assert resumed.status.value == "completed"
    # The rejected tool never executed.
    assert calls == []
    # The resumed model turn carried the operator feedback + the rejected tool name.
    injected = "\n".join(provider.seen_messages)
    assert "use the staging table instead" in injected
    assert "rejected" in injected.lower()


@pytest.mark.asyncio
async def test_reject_terminates_when_flag_disabled() -> None:
    registry, calls = _executed_calls_registry()
    runner = _make_runner(
        RunnerConfig(  # corrective_rejection_enabled defaults to False
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )
    paused = await _pause_on_danger(runner, "run_reject_default")
    assert paused.interrupt is not None

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_reject_default",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
                message="please stop",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_bare_reject_terminates_even_when_enabled() -> None:
    registry, calls = _executed_calls_registry()
    runner = _make_runner(
        RunnerConfig(
            corrective_rejection_enabled=True,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )
    paused = await _pause_on_danger(runner, "run_reject_bare")
    assert paused.interrupt is not None

    resumed = await runner.run(
        AgentRunInput(
            run_id="run_reject_bare",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,  # no message -> hard abort
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "failed"
    assert calls == []
