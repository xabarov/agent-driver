"""Offline CodeAgent eval-like tests for replay and safety."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import FakeRestrictedCodeExecutor
from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
)
from agent_driver.evals import render_cli_replay
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


@pytest.mark.asyncio
async def test_code_agent_arithmetic_eval_case() -> None:
    """Fake code agent should pass arithmetic case offline."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="calc",
            run_id="run_eval_code_math",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "final_answer(40 + 2)"}},
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["tool_results"][0]["summary"] == "42"


@pytest.mark.asyncio
async def test_code_agent_replay_contains_action_events() -> None:
    """Code-agent run should keep deterministic replay event sequence."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="replay",
            run_id="run_eval_code_replay",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "print('x')\nfinal_answer('ok')"}},
        )
    )
    replay = render_cli_replay(output)
    assert "run_started" in replay
    assert "tool_call_completed" in replay
    assert "run_completed" in replay
    assert "[1] run_started" in replay
