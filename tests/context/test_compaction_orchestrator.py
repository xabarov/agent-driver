"""Compaction orchestrator and eligibility tests."""

from __future__ import annotations

from agent_driver.context.compaction import (
    CompactionOrchestrator,
    build_partial_compaction,
    evaluate_session_memory_freshness,
)
from agent_driver.contracts import (
    CompactionMode,
    CompactionResult,
    CompactionSkipReason,
    SessionMemory,
)


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


def test_orchestrator_failure_counter_resets_after_success() -> None:
    """Failure counter should reset after successful completed attempt."""
    orchestrator = CompactionOrchestrator(failure_limit=3)
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=False,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=None,
    )
    compaction_id = orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=decision,
        result=CompactionResult(
            compaction_id=compaction_id,
            mode=CompactionMode.LLM_FULL,
            success=False,
            metadata={"failure": "x"},
        ),
        failures=[{"kind": "forced"}],
    )
    assert orchestrator._consecutive_failures == 1  # pylint: disable=protected-access
    compaction_id = orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=decision,
        result=CompactionResult(
            compaction_id=compaction_id,
            mode=CompactionMode.LLM_FULL,
            success=True,
        ),
    )
    assert orchestrator._consecutive_failures == 0  # pylint: disable=protected-access


def test_orchestrator_state_snapshot_reports_circuit_breaker() -> None:
    """State snapshot should expose lock/failure/circuit fields."""
    orchestrator = CompactionOrchestrator(failure_limit=1)
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=False,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=None,
    )
    compaction_id = orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=decision,
        result=CompactionResult(
            compaction_id=compaction_id,
            mode=CompactionMode.LLM_FULL,
            success=False,
            metadata={"failure": "forced"},
        ),
        failures=[{"kind": "forced"}],
    )
    snapshot = orchestrator.state_snapshot()
    assert snapshot["failure_limit"] == 1
    assert snapshot["consecutive_failures"] == 1
    assert snapshot["circuit_breaker_open"] is True


def test_partial_compaction_prefix_summary_keeps_recent_tail() -> None:
    """Partial compaction should summarize prefix and keep recent tail."""
    messages = [{"role": "user", "content": f"msg-{idx}"} for idx in range(10)]
    out = build_partial_compaction(
        messages=messages,
        retain_recent_messages=4,
        prefix_mode=True,
    )
    assert out.prompt_messages[0]["role"] == "system"
    assert "Partial compaction summary" in out.prompt_messages[0]["content"]
    assert len(out.prompt_messages) == 5


def test_partial_compaction_preserves_leading_system_policy() -> None:
    """Partial compaction must not summarize away the stable system prompt."""
    messages = [
        {"role": "system", "content": "Base ReAct policy. Keep me verbatim."},
    ]
    messages.extend({"role": "user", "content": f"old-{idx}"} for idx in range(8))

    out = build_partial_compaction(
        messages=messages,
        retain_recent_messages=3,
        prefix_mode=True,
    )

    assert out.prompt_messages[0] == {
        "role": "system",
        "content": "Base ReAct policy. Keep me verbatim.",
    }
    assert out.prompt_messages[1]["role"] == "system"
    assert "Partial compaction summary" in out.prompt_messages[1]["content"]
    assert [item["content"] for item in out.prompt_messages[-3:]] == [
        "old-5",
        "old-6",
        "old-7",
    ]
    assert out.metadata["protected_head_count"] == 1


def _fail_attempt(orchestrator: CompactionOrchestrator) -> None:
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=True,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=_session_memory(),
    )
    orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=decision,
        failures=[{"kind": "unit", "message": "boom"}],
    )


def test_circuit_breaker_cooldown_then_half_open_probe() -> None:
    """Epic 017 (reference: openclaude autoCompact): open breaker skips attempts for
    ``cooldown_attempts`` checks, then lets ONE half-open probe through; probe success
    closes the breaker, probe failure re-opens it with a fresh cooldown."""
    orchestrator = CompactionOrchestrator(failure_limit=2, cooldown_attempts=2)
    _fail_attempt(orchestrator)
    _fail_attempt(orchestrator)
    assert orchestrator.state_snapshot()["circuit_breaker_open"] is True
    assert orchestrator.state_snapshot()["cooldown_remaining"] == 2

    def _decide():
        return orchestrator.decide(
            enable_compaction=True,
            enable_session_memory_compaction=True,
            enable_llm_compaction=True,
            token_pressure_state="blocking",
            session_memory=_session_memory(),
        )

    # Two cooldown ticks: breaker stays closed to attempts (eligible False).
    assert _decide().eligible is False
    assert _decide().eligible is False
    # Cooldown elapsed → half-open probe allowed through.
    probe = _decide()
    assert probe.eligible is True
    assert orchestrator.state_snapshot()["half_open_probe"] is True
    # Probe SUCCESS closes the breaker fully.
    orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=probe,
        result=CompactionResult(
            compaction_id="cmp_ok",
            mode=CompactionMode.SESSION_MEMORY,
            success=True,
        ),
    )
    snapshot = orchestrator.state_snapshot()
    assert snapshot["circuit_breaker_open"] is False
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["half_open_probe"] is False


def test_circuit_breaker_failed_probe_reopens_with_fresh_cooldown() -> None:
    orchestrator = CompactionOrchestrator(failure_limit=2, cooldown_attempts=1)
    _fail_attempt(orchestrator)
    _fail_attempt(orchestrator)

    def _decide():
        return orchestrator.decide(
            enable_compaction=True,
            enable_session_memory_compaction=True,
            enable_llm_compaction=True,
            token_pressure_state="blocking",
            session_memory=_session_memory(),
        )

    assert _decide().eligible is False  # cooldown tick
    probe = _decide()
    assert probe.eligible is True  # half-open
    orchestrator.start_attempt()
    orchestrator.complete_attempt(
        decision=probe, failures=[{"kind": "unit", "message": "still broken"}]
    )
    snapshot = orchestrator.state_snapshot()
    assert snapshot["circuit_breaker_open"] is True
    assert snapshot["cooldown_remaining"] == 1  # fresh cooldown armed
