"""Tests for compaction outcome and circuit-breaker emission helpers."""

from __future__ import annotations

from agent_driver.context.compaction import CompactionOrchestrator
from agent_driver.contracts import (
    AgentRunInput,
    CompactionDecision,
    CompactionMode,
    CompactionResult,
)
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.compaction_stage import (
    _emit_compaction_outcome,
    _emit_compaction_started,
    _maybe_emit_circuit_breaker_warning,
)
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class _CaptureHost:
    """Minimal host stub that records emitted EventSpec instances."""

    def __init__(self) -> None:
        self.events: list[EventSpec] = []

    def _emit(self, event: EventSpec) -> None:
        self.events.append(event)


def _make_context() -> RunContext:
    run_input = AgentRunInput(
        input="hello",
        run_id="run_compaction_1",
        agent_id="agent",
        graph_preset="single_react",
    )
    return RunContext(
        run_input=run_input,
        identifiers={"run_id": "run_compaction_1", "attempt_id": "att_1"},
        metadata={},
    )


def _force_failure(orchestrator: CompactionOrchestrator) -> None:
    """Drive the orchestrator through one failed attempt without going via the stage."""
    decision = orchestrator.decide(
        enable_compaction=True,
        enable_session_memory_compaction=False,
        enable_llm_compaction=True,
        token_pressure_state="blocking",
        session_memory=None,
    )
    if not decision.eligible:
        return
    cid = orchestrator.start_attempt()
    result = CompactionResult(
        compaction_id=cid,
        mode=CompactionMode.LLM_FULL,
        success=False,
    )
    orchestrator.complete_attempt(
        decision=decision,
        result=result,
        failures=[{"kind": "forced"}],
    )


# ---------------------------------------------------------------------------
# _emit_compaction_started
# ---------------------------------------------------------------------------


def test_emit_compaction_started_writes_lifecycle_event() -> None:
    """Helper emits MEMORY_COMPACTION_STARTED before the terminal outcome."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator()
    decision = CompactionDecision(
        eligible=True,
        mode=CompactionMode.PARTIAL,
        metadata={"token_pressure_state": "blocking"},
    )
    _emit_compaction_started(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        decision=decision,
        compaction_id="cmp_started",
        token_pressure_state="blocking",
        orchestrator=orchestrator,
    )

    assert len(host.events) == 1
    event = host.events[0]
    assert event.event_type is RuntimeEventType.MEMORY_COMPACTION_STARTED
    payload = event.payload or {}
    assert payload["compaction_id"] == "cmp_started"
    assert payload["mode"] == "partial"
    assert payload["reason"] == "token_pressure"
    assert payload["token_pressure_state"] == "blocking"
    assert payload["compaction_state"]["circuit_breaker_open"] is False


# ---------------------------------------------------------------------------
# _emit_compaction_outcome
# ---------------------------------------------------------------------------


def test_emit_compaction_outcome_writes_memory_compacted_event() -> None:
    """Helper emits one MEMORY_COMPACTED event with outcome and state snapshot."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator()
    _emit_compaction_outcome(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        outcome="skipped",
        payload_extras={"mode": "session_memory", "skip_reason": "not_eligible"},
        orchestrator=orchestrator,
    )
    assert len(host.events) == 1
    event = host.events[0]
    assert event.event_type is RuntimeEventType.MEMORY_COMPACTED
    payload = event.payload or {}
    assert payload["outcome"] == "skipped"
    assert payload["mode"] == "session_memory"
    assert payload["skip_reason"] == "not_eligible"
    state = payload["compaction_state"]
    assert state["consecutive_failures"] == 0
    assert state["circuit_breaker_open"] is False
    assert state["failure_limit"] == orchestrator.failure_limit


def test_emit_compaction_outcome_carries_orchestrator_state_after_failures() -> None:
    """compaction_state reflects the orchestrator after each attempt."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=3)
    _force_failure(orchestrator)
    _emit_compaction_outcome(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        outcome="failed",
        payload_extras={"mode": "llm_full", "compaction_id": "cmp_1"},
        orchestrator=orchestrator,
    )
    payload = host.events[0].payload or {}
    assert payload["outcome"] == "failed"
    assert payload["compaction_state"]["consecutive_failures"] == 1
    assert payload["compaction_state"]["circuit_breaker_open"] is False


def test_emit_compaction_outcome_reports_circuit_breaker_open_state() -> None:
    """When failures cross failure_limit, the snapshot shows circuit_breaker_open=True."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=1)
    _force_failure(orchestrator)
    _emit_compaction_outcome(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        outcome="failed",
        payload_extras={"mode": "llm_full"},
        orchestrator=orchestrator,
    )
    payload = host.events[0].payload or {}
    assert payload["compaction_state"]["circuit_breaker_open"] is True


# ---------------------------------------------------------------------------
# _maybe_emit_circuit_breaker_warning
# ---------------------------------------------------------------------------


def test_circuit_breaker_warning_emitted_on_transition() -> None:
    """When circuit-breaker transitions from closed to open, WARNING is emitted."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=1)
    _force_failure(orchestrator)  # this opens the breaker
    _maybe_emit_circuit_breaker_warning(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        before_open=False,
        orchestrator=orchestrator,
    )
    assert len(host.events) == 1
    event = host.events[0]
    assert event.event_type is RuntimeEventType.WARNING
    payload = event.payload or {}
    assert payload["kind"] == "compaction_circuit_breaker"
    assert payload["signal_id"] == "compaction_circuit_breaker_open"
    assert payload["severity"] == "critical"
    assert payload["consecutive_failures"] == 1
    assert payload["failure_limit"] == 1


def test_circuit_breaker_warning_not_emitted_when_already_open() -> None:
    """No transition warning when the breaker was already open."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=1)
    _force_failure(orchestrator)  # opens
    _maybe_emit_circuit_breaker_warning(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        before_open=True,
        orchestrator=orchestrator,
    )
    assert not host.events


def test_circuit_breaker_warning_not_emitted_when_still_closed() -> None:
    """No transition warning when the breaker remains closed."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=5)
    _force_failure(orchestrator)  # one failure, still below limit
    _maybe_emit_circuit_breaker_warning(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        before_open=False,
        orchestrator=orchestrator,
    )
    assert not host.events


def test_circuit_breaker_warning_severity_is_critical() -> None:
    """The transition warning carries critical severity for host alerts."""
    host = _CaptureHost()
    orchestrator = CompactionOrchestrator(failure_limit=2)
    _force_failure(orchestrator)
    _force_failure(orchestrator)
    _maybe_emit_circuit_breaker_warning(
        host,  # type: ignore[arg-type]
        context=_make_context(),
        before_open=False,
        orchestrator=orchestrator,
    )
    assert host.events[0].payload["severity"] == "critical"


# ---------------------------------------------------------------------------
# Projector recognizes compaction_circuit_breaker kind
# ---------------------------------------------------------------------------


def test_warning_projector_recognizes_compaction_circuit_breaker() -> None:
    """project_warning_event projects the new compaction_circuit_breaker kind."""
    from agent_driver.adapters import project_warning_event
    from agent_driver.contracts import RunStreamEvent

    event = RunStreamEvent(
        schema_version="1.0",
        stream_id="run_1:1",
        run_id="run_1",
        attempt_id="att_1",
        seq=1,
        event="warning",
        source="runtime_event",
        data={
            "kind": "compaction_circuit_breaker",
            "signal_id": "compaction_circuit_breaker_open",
            "severity": "critical",
            "description": "transition reason",
            "consecutive_failures": 3,
            "failure_limit": 3,
        },
        runtime_event_id="evt_1",
        created_at="2026-05-21T00:00:00Z",
    )
    projection = project_warning_event(event)
    assert projection is not None
    assert projection["kind"] == "compaction_circuit_breaker"
    assert projection["signal_id"] == "compaction_circuit_breaker_open"
    assert projection["severity"] == "critical"
    data = projection["data"]
    assert data["consecutive_failures"] == 3
    assert data["failure_limit"] == 3
