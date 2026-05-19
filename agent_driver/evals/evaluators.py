"""Deterministic evaluators for runtime/e2e report harness."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.evals.contracts import BudgetLimits, EvaluatorResult
from agent_driver.observability import build_trace_export


def evaluate_event_schema(output: AgentRunOutput) -> EvaluatorResult:
    """Validate monotonic sequence and run-attempt consistency for events."""
    seqs = [event.seq for event in output.events]
    monotonic = all(curr > prev for prev, curr in zip(seqs, seqs[1:]))
    consistent_identity = all(
        event.run_id == output.run_id and event.attempt_id == output.attempt_id
        for event in output.events
    )
    return EvaluatorResult(
        evaluator="event_schema",
        passed=monotonic and consistent_identity,
        details={
            "event_count": len(output.events),
            "monotonic_seq": monotonic,
            "consistent_identity": consistent_identity,
        },
    )


def evaluate_terminal_state(output: AgentRunOutput) -> EvaluatorResult:
    """Validate terminal status invariants against terminal reason/events."""
    terminal_statuses = {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }
    terminal = output.status in terminal_statuses
    has_reason = output.terminal_reason is not None
    terminal_events = {"run_completed", "run_failed", "run_cancelled"}
    has_terminal_event = any(
        event.type.value in terminal_events for event in output.events
    )
    passed = (not terminal) or (has_reason and has_terminal_event)
    return EvaluatorResult(
        evaluator="terminal_state",
        passed=passed,
        details={
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
            "has_terminal_event": has_terminal_event,
        },
    )


def evaluate_tool_policy(output: AgentRunOutput) -> EvaluatorResult:
    """Validate tool-policy/HITL trajectory coherence from traces/events."""
    has_interrupt_event = any(
        event.type.value == "interrupt_requested" for event in output.events
    )
    denied_or_failed = any(
        trace.status.value in {"denied", "failed"} for trace in output.tool_trace
    )
    paused_with_interrupt = (
        output.status == RunStatus.PAUSED and output.interrupt is not None
    )
    passed = not has_interrupt_event or paused_with_interrupt or denied_or_failed
    return EvaluatorResult(
        evaluator="tool_policy",
        passed=passed,
        details={
            "interrupt_events": int(has_interrupt_event),
            "paused_with_interrupt": paused_with_interrupt,
            "denied_or_failed_trace": denied_or_failed,
            "trace_count": len(output.tool_trace),
        },
    )


def evaluate_checkpoint_replay(output: AgentRunOutput) -> EvaluatorResult:
    """Validate replay-projection coherence from exported trace and checkpoint."""
    trace_export = build_trace_export(output)
    sorted_by_seq = [span.seq for span in trace_export.spans] == sorted(
        span.seq for span in trace_export.spans
    )
    checkpoint_matches = output.checkpoint is None or (
        output.checkpoint.run_id == output.run_id
        and output.checkpoint.attempt_id == output.attempt_id
    )
    passed = sorted_by_seq and checkpoint_matches
    return EvaluatorResult(
        evaluator="checkpoint_replay",
        passed=passed,
        details={
            "trace_id": trace_export.trace_id,
            "span_count": len(trace_export.spans),
            "checkpoint_present": output.checkpoint is not None,
            "checkpoint_matches_identity": checkpoint_matches,
        },
    )


def _duration_ms(started_at: str, ended_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = int((end - start).total_seconds() * 1000)
    return max(delta, 0)


def evaluate_cost_latency_budget(
    output: AgentRunOutput, *, limits: BudgetLimits | None = None
) -> EvaluatorResult:
    """Validate optional token/cost/latency budgets."""
    active_limits = limits or BudgetLimits()
    total_tokens = output.usage.total_tokens if output.usage is not None else 0
    cost = output.usage.cost_usd_estimate if output.usage is not None else None
    latency_ms: int | None = None
    if output.events:
        ordered = sorted(output.events, key=lambda event: event.seq)
        latency_ms = _duration_ms(ordered[0].created_at, ordered[-1].created_at)

    failures: list[str] = []
    if (
        active_limits.max_total_tokens is not None
        and total_tokens > active_limits.max_total_tokens
    ):
        failures.append("max_total_tokens")
    if (
        active_limits.max_cost_usd is not None
        and cost is not None
        and cost > active_limits.max_cost_usd
    ):
        failures.append("max_cost_usd")
    if (
        active_limits.max_latency_ms is not None
        and latency_ms is not None
        and latency_ms > active_limits.max_latency_ms
    ):
        failures.append("max_latency_ms")

    return EvaluatorResult(
        evaluator="cost_latency_budget",
        passed=not failures,
        details={
            "total_tokens": total_tokens,
            "cost_usd_estimate": cost,
            "latency_ms": latency_ms,
            "violations": failures,
        },
    )


def default_evaluators(
    *, limits: BudgetLimits | None = None
) -> list[Callable[[AgentRunOutput], EvaluatorResult]]:
    """Return default deterministic evaluator set for local runner."""
    return [
        evaluate_event_schema,
        evaluate_terminal_state,
        evaluate_tool_policy,
        evaluate_checkpoint_replay,
        lambda output: evaluate_cost_latency_budget(output, limits=limits),
    ]
