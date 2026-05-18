"""Runtime metadata integration tests for Phase-6 refs."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
)


@pytest.mark.asyncio
async def test_runtime_output_exposes_phase6_artifact_and_digest_refs() -> None:
    """Runtime output metadata should include artifact_refs and digest_refs keys."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_phase6_meta",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert "artifact_refs" in output.metadata
    assert "digest_refs" in output.metadata
    assert isinstance(output.metadata["artifact_refs"], list)
    assert isinstance(output.metadata["digest_refs"], list)
