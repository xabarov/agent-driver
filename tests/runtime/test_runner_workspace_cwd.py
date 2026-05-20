"""Tests for run-scoped workspace cwd context in runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    fake_noop_tool_executor,
)
from agent_driver.tools.context import get_workspace_cwd


@pytest.mark.asyncio
async def test_runner_sets_and_resets_workspace_cwd_from_app_metadata(tmp_path) -> None:
    seen: list[Path] = []

    async def _capturing_executor(run_input: AgentRunInput, llm_response: LlmResponse):
        _ = run_input
        _ = llm_response
        seen.append(get_workspace_cwd())
        return await fake_noop_tool_executor(run_input, llm_response)

    provider = FakeProvider(response_text="ok")
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=_capturing_executor),
    )

    before = get_workspace_cwd()
    out = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_workspace_cwd_1",
            agent_id="agent-test",
            graph_preset="single_react",
            app_metadata={"workspace_cwd": str(tmp_path)},
        )
    )
    after = get_workspace_cwd()

    assert out.status.value == "completed"
    assert seen == [tmp_path.resolve()]
    assert after == before
