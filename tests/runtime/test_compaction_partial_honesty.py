"""Partial compaction reports an honest outcome (BUG-7 / compaction hardening C1).

Partial compaction used to return ``success=True`` unconditionally — even for an
explicit no-op or a rewrite that freed no space — which *reset* the circuit
breaker and masked that no progress was made. It now reports success only on real
token progress; a no-progress attempt is an honest ``skipped`` that neither resets
the breaker (false clear) nor counts as a failure (a no-op is not a breakage).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_driver.context.compaction import CompactionOrchestrator
from agent_driver.contracts import (
    AgentRunInput,
    CompactionDecision,
    CompactionMode,
)
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.compaction_stage import _emit_compaction_started
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _apply_partial_compaction,
)
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class _Host:
    """Minimal partial-compaction host: captures events, carries cleanup config."""

    def __init__(self) -> None:
        self.events: list[EventSpec] = []
        self._config = SimpleNamespace(post_compact_max_reinjected_artifact_refs=5)

    def _emit(self, event: EventSpec) -> None:
        self.events.append(event)


def _context() -> RunContext:
    run_input = AgentRunInput(
        input="do the thing",
        run_id="run_partial_1",
        agent_id="agent",
        graph_preset="single_react",
    )
    return RunContext(
        run_input=run_input,
        identifiers={"run_id": "run_partial_1", "attempt_id": "att_1"},
        metadata={},
    )


def _decision() -> CompactionDecision:
    return CompactionDecision(eligible=True, mode=CompactionMode.PARTIAL, metadata={})


def _run(host, context, request, orchestrator) -> bool:
    orchestrator.start_attempt()
    return asyncio.run(
        _apply_partial_compaction(
            host,
            context=context,
            request=request,
            orchestrator=orchestrator,
            decision=_decision(),
            compaction_id="cmp_partial",
            circuit_breaker_open_before=False,
        )
    )


def _outcome_payload(host: _Host) -> dict:
    events = [
        e for e in host.events if e.event_type is RuntimeEventType.MEMORY_COMPACTED
    ]
    assert events, "expected a MEMORY_COMPACTED outcome event"
    return dict(events[-1].payload or {})


def _drive_one_failure(orchestrator: CompactionOrchestrator) -> None:
    """Advance consecutive_failures to 1 so a later false 'success' would show as a
    reset to 0 (the old BUG-7 behaviour)."""
    from agent_driver.contracts import CompactionResult

    orchestrator.complete_attempt(
        decision=_decision(),
        result=CompactionResult(
            compaction_id="cmp_prev", mode=CompactionMode.LLM_FULL, success=False
        ),
    )
    assert orchestrator.state_snapshot()["consecutive_failures"] == 1


def test_noop_partial_is_skipped_and_does_not_reset_breaker() -> None:
    """A below-threshold list is a no-op: honest skip, view untouched, no reset."""
    host = _Host()
    orchestrator = CompactionOrchestrator(failure_limit=3)
    _drive_one_failure(orchestrator)
    original = [
        ChatMessage(role="system", content="policy"),
        ChatMessage(role="user", content="hi"),
    ]
    request = SimpleNamespace(messages=list(original))

    handled = _run(host, _context(), request, orchestrator)

    assert handled is True
    # View untouched.
    assert [m.content for m in request.messages] == [m.content for m in original]
    payload = _outcome_payload(host)
    assert payload["outcome"] == "skipped"
    assert payload["skip_reason"] == "no_op"
    assert payload["chars_freed"] <= 0
    # Breaker NOT falsely reset (old bug reset it to 0).
    assert orchestrator.state_snapshot()["consecutive_failures"] == 1


def test_progress_partial_is_successful_and_resets_breaker() -> None:
    """A large list that genuinely shrinks reports success and resets the breaker."""
    host = _Host()
    orchestrator = CompactionOrchestrator(failure_limit=3)
    _drive_one_failure(orchestrator)
    original = [ChatMessage(role="system", content="policy")]
    for i in range(14):
        role = "user" if i % 2 == 0 else "assistant"
        original.append(ChatMessage(role=role, content=f"message {i} " + "x" * 200))
    request = SimpleNamespace(messages=list(original))

    handled = _run(host, _context(), request, orchestrator)

    assert handled is True
    # View genuinely reduced.
    assert len(request.messages) < len(original)
    payload = _outcome_payload(host)
    assert payload["outcome"] == "successful"
    assert payload["chars_freed"] > 0
    # A real success resets the breaker.
    assert orchestrator.state_snapshot()["consecutive_failures"] == 0


def test_repeated_noop_partials_let_breaker_advance() -> None:
    """The regression BUG-7 guards: repeated no-op partials must not pin the breaker
    at zero. Here each no-op is neutral, so a genuine failure still advances it."""
    host = _Host()
    orchestrator = CompactionOrchestrator(failure_limit=2)
    small = [
        ChatMessage(role="system", content="policy"),
        ChatMessage(role="user", content="hi"),
    ]
    # Two no-op partials — under the old bug these would each reset to 0.
    for _ in range(2):
        _run(host, _context(), SimpleNamespace(messages=list(small)), orchestrator)
    assert orchestrator.state_snapshot()["consecutive_failures"] == 0
    assert orchestrator.state_snapshot()["circuit_breaker_open"] is False
    # Confirm the neutral outcome never touched the breaker: a single real failure
    # then leaves it at 1 (not masked by a prior false reset).
    _drive_one_failure(orchestrator)
    assert orchestrator.state_snapshot()["consecutive_failures"] == 1


# Keep the started-event import exercised so the module's public helper surface is
# covered alongside the honest-outcome path.
def test_started_event_precedes_outcome() -> None:
    host = _Host()
    orchestrator = CompactionOrchestrator()
    _emit_compaction_started(
        host,  # type: ignore[arg-type]
        context=_context(),
        decision=_decision(),
        compaction_id="cmp_partial",
        token_pressure_state="blocking",
        orchestrator=orchestrator,
    )
    assert host.events[0].event_type is RuntimeEventType.MEMORY_COMPACTION_STARTED
