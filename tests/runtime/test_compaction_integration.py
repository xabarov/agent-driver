"""Runtime integration tests for compaction feature flag."""

from __future__ import annotations

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


@pytest.mark.asyncio
async def test_compaction_disabled_keeps_default_behavior() -> None:
    """Default config should not force compaction events."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_compaction_off",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "completed"
    assert output.metadata is not None
    assert output.metadata.get("compaction_decision", {}).get("skip_reason") in {
        None,
        "disabled",
    }


@pytest.mark.asyncio
async def test_compaction_flag_records_decision_and_audit() -> None:
    """Compaction-enabled run should record decision and audit metadata."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            enable_compaction=True,
            enable_llm_compaction=True,
            token_compact_threshold=1,
            token_blocking_threshold=2,
            context_window_estimate=100,
            output_token_reserve=1,
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello " * 100,
            run_id="run_compaction_on",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.metadata is not None
    assert "compaction_decision" in output.metadata
    assert "compaction_audit" in output.metadata
    failures = output.metadata.get("compaction_failures")
    assert isinstance(failures, list)
    assert failures
    assert failures[0]["kind"] == "llm_compaction_failed"


@pytest.mark.asyncio
async def test_partial_compaction_path_runs_when_llm_compaction_disabled() -> None:
    """Compaction should use partial mode when full LLM path is disabled."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            enable_compaction=True,
            enable_llm_compaction=False,
            enable_session_memory_compaction=False,
            token_compact_threshold=1,
            token_blocking_threshold=2,
            context_window_estimate=100,
            output_token_reserve=1,
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello " * 100,
            run_id="run_compaction_partial",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.metadata is not None
    assert output.metadata["compaction_result"]["mode"] == "partial"
    assert output.metadata["compaction_failures"] == []
    assert "post_compact_cleanup" in output.memory_audit
