"""Deterministic evaluator tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RuntimeEventType,
    UsageSummary,
    new_runtime_event,
)
from agent_driver.evals import (
    BudgetLimits,
    evaluate_checkpoint_replay,
    evaluate_cost_latency_budget,
    evaluate_event_schema,
    evaluate_terminal_state,
    evaluate_tool_policy,
)


def _output(
    *, status: str = "completed", terminal_reason: str | None = "final_answer"
) -> AgentRunOutput:
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_eval_1", "attempt_id": "attempt_1", "seq": 1},
        ),
        new_runtime_event(
            event_type=RuntimeEventType.RUN_COMPLETED,
            context={"run_id": "run_eval_1", "attempt_id": "attempt_1", "seq": 2},
        ),
    ]
    return AgentRunOutput(
        run_id="run_eval_1",
        attempt_id="attempt_1",
        status=status,
        terminal_reason=terminal_reason,
        events=events,
    )


def test_event_schema_evaluator_passes_monotonic_events() -> None:
    """Event schema evaluator should pass ordered run events."""
    result = evaluate_event_schema(_output())
    assert result.passed
    assert result.details["monotonic_seq"]


def test_terminal_state_evaluator_accepts_valid_terminal_output() -> None:
    """Terminal evaluator should pass for contract-valid terminal outputs."""
    result = evaluate_terminal_state(_output())
    assert result.passed


def test_tool_policy_evaluator_passes_without_interrupt_events() -> None:
    """Tool policy evaluator should pass when no interrupt trajectory exists."""
    result = evaluate_tool_policy(_output())
    assert result.passed


def test_checkpoint_replay_evaluator_reports_trace_span_count() -> None:
    """Checkpoint/replay evaluator should include trace projection details."""
    result = evaluate_checkpoint_replay(_output())
    assert result.passed
    assert result.details["span_count"] == 2


def test_budget_evaluator_detects_token_violation() -> None:
    """Budget evaluator should fail when token budget is exceeded."""
    payload = _output()
    payload.usage = UsageSummary(input_tokens=20, output_tokens=10, total_tokens=30)
    result = evaluate_cost_latency_budget(
        payload, limits=BudgetLimits(max_total_tokens=10)
    )
    assert not result.passed
    assert "max_total_tokens" in result.details["violations"]
