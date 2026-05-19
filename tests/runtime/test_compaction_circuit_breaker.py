"""Compaction circuit-breaker behavior tests."""

from __future__ import annotations

from agent_driver.context.compaction import CompactionOrchestrator
from agent_driver.contracts import CompactionSkipReason


def test_compaction_circuit_breaker_opens_after_failures() -> None:
    """Orchestrator should skip when failure limit is reached."""
    orchestrator = CompactionOrchestrator(failure_limit=1)
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=False,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=None,
    )
    audit = orchestrator.execute_placeholder(decision)
    assert audit.failures
    second = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=False,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=None,
    )
    assert second.eligible is False
    assert second.skip_reason == CompactionSkipReason.CIRCUIT_BREAKER_OPEN
