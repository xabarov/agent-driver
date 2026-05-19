"""Compaction orchestrator and eligibility tests."""

from __future__ import annotations

from agent_driver.context.compaction import (
    CompactionOrchestrator,
    evaluate_session_memory_freshness,
)
from agent_driver.contracts import CompactionMode, CompactionSkipReason, SessionMemory


def _session_memory(last_turn: int = 6) -> SessionMemory:
    return SessionMemory(
        memory_id="mem_1",
        session_id="session_1",
        summary="Summary",
        key_facts=["f1"],
        pending_tasks=[],
        open_questions=[],
        last_summarized_turn_index=last_turn,
    )


def test_orchestrator_skips_when_disabled() -> None:
    """Compaction disabled should always yield skip decision."""
    orchestrator = CompactionOrchestrator()
    decision = orchestrator.decide(
        enable_compaction=False,
        enable_session_memory_compaction=True,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=_session_memory(),
    )
    assert decision.eligible is False
    assert decision.skip_reason == CompactionSkipReason.DISABLED


def test_orchestrator_selects_session_memory_on_pressure() -> None:
    """Compaction should select session-memory path when fresh memory exists."""
    orchestrator = CompactionOrchestrator()
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=True,
        enable_llm_compaction=True,
        token_pressure_state="compact_recommended",
        session_memory=_session_memory(),
    )
    assert decision.eligible is True
    assert decision.mode == CompactionMode.SESSION_MEMORY


def test_orchestrator_lock_prevents_reentry() -> None:
    """Lock should prevent concurrent compaction attempts."""
    orchestrator = CompactionOrchestrator()
    orchestrator._lock_active = True  # pylint: disable=protected-access
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=True,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=_session_memory(),
    )
    assert decision.eligible is False
    assert decision.skip_reason == CompactionSkipReason.LOCKED


def test_session_memory_freshness_classification() -> None:
    """Freshness policy should classify memory deterministically."""
    fresh = evaluate_session_memory_freshness(
        session_memory=_session_memory(last_turn=8),
        latest_turn_index=10,
        stale_after_turns=4,
    )
    stale = evaluate_session_memory_freshness(
        session_memory=_session_memory(last_turn=1),
        latest_turn_index=10,
        stale_after_turns=4,
    )
    assert fresh.state == "fresh"
    assert stale.state == "stale"
