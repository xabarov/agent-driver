"""Pure bounded-goal supervision decisions."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.runtime_decisions import (
    GoalContract,
    GoalEvaluatorResult,
    GoalState,
    RuntimeDecision,
)


def evaluate_goal_supervision(
    *,
    run_id: str,
    attempt_id: str,
    seq: int,
    goal: GoalContract,
    evaluator_result: GoalEvaluatorResult | None = None,
    turn_count: int = 0,
    tool_calls: int = 0,
    cost_usd: float | None = None,
    wall_seconds: float | None = None,
) -> tuple[GoalState, RuntimeDecision]:
    """Resolve one trace-safe bounded-goal supervision decision.

    This is intentionally side-effect-free. Runtime loops can call it later, but
    deterministic tests can already prove the stop/continue semantics without
    spending provider budget or adding autonomy.
    """

    budget = {
        "turn_count": turn_count,
        "tool_calls": tool_calls,
        "cost_usd": cost_usd,
        "wall_seconds": wall_seconds,
        "max_turns": goal.max_turns,
        "max_tool_calls": goal.max_tool_calls,
        "max_cost_usd": goal.max_cost_usd,
        "max_wall_seconds": goal.max_wall_seconds,
    }
    exceeded = _exceeded_budget(goal, budget)
    if exceeded:
        decision = _decision(
            run_id=run_id,
            attempt_id=attempt_id,
            seq=seq,
            goal=goal,
            action="mark_blocked",
            reason=f"goal_budget_exceeded:{exceeded}",
            status="failed",
            budget=budget,
        )
        return _state(goal, "blocked", decision, budget, evaluator_result), decision

    if evaluator_result is not None and evaluator_result.status == "achieved":
        decision = _decision(
            run_id=run_id,
            attempt_id=attempt_id,
            seq=seq,
            goal=goal,
            action="mark_achieved",
            reason=evaluator_result.reason,
            status="satisfied",
            budget=budget,
            observed_evidence=evaluator_result.observed_evidence,
        )
        return _state(goal, "achieved", decision, budget, evaluator_result), decision

    if evaluator_result is not None and evaluator_result.status in {"blocked", "failed"}:
        decision = _decision(
            run_id=run_id,
            attempt_id=attempt_id,
            seq=seq,
            goal=goal,
            action="mark_blocked",
            reason=evaluator_result.reason,
            status="failed",
            budget=budget,
            required_evidence=evaluator_result.missing_evidence,
            observed_evidence=evaluator_result.observed_evidence,
        )
        return _state(goal, "blocked", decision, budget, evaluator_result), decision

    missing_evidence = _missing_required_evidence(goal, evaluator_result)
    if missing_evidence:
        decision = _decision(
            run_id=run_id,
            attempt_id=attempt_id,
            seq=seq,
            goal=goal,
            action="continue",
            reason="goal_required_evidence_unsatisfied",
            status="proposed",
            budget=budget,
            required_evidence=missing_evidence,
            observed_evidence=(
                evaluator_result.observed_evidence if evaluator_result else []
            ),
        )
        return _state(goal, "active", decision, budget, evaluator_result), decision

    decision = _decision(
        run_id=run_id,
        attempt_id=attempt_id,
        seq=seq,
        goal=goal,
        action="continue",
        reason="goal_in_progress",
        status="proposed",
        budget=budget,
        observed_evidence=evaluator_result.observed_evidence
        if evaluator_result is not None
        else [],
    )
    return _state(goal, "active", decision, budget, evaluator_result), decision


def _decision(
    *,
    run_id: str,
    attempt_id: str,
    seq: int,
    goal: GoalContract,
    action: str,
    reason: str,
    status: str,
    budget: dict[str, Any],
    required_evidence: list[str] | None = None,
    observed_evidence: list[str] | None = None,
) -> RuntimeDecision:
    return RuntimeDecision(
        decision_id=f"dec_goal_{seq}",
        run_id=run_id,
        attempt_id=attempt_id,
        seq=seq,
        kind="goal",
        trigger="budget_threshold" if reason.startswith("goal_budget") else "finalize",
        action=action,
        reason=reason,
        status=status,
        goal_id=goal.goal_id,
        policy_id="goal_supervision",
        budget=budget,
        required_evidence=required_evidence or goal.required_evidence,
        observed_evidence=observed_evidence or [],
    )


def _state(
    goal: GoalContract,
    status: str,
    decision: RuntimeDecision,
    budget: dict[str, Any],
    evaluator_result: GoalEvaluatorResult | None,
) -> GoalState:
    return GoalState(
        goal_id=goal.goal_id,
        status=status,
        turn_count=int(budget.get("turn_count") or 0),
        last_decision_id=decision.decision_id,
        last_evaluator_result=(
            evaluator_result.status if evaluator_result is not None else None
        ),
        continuation_instruction=(
            "continue" if decision.action == "continue" else None
        ),
        budget=budget,
    )


def _exceeded_budget(goal: GoalContract, budget: dict[str, Any]) -> str | None:
    if goal.max_turns is not None and int(budget["turn_count"] or 0) >= goal.max_turns:
        return "max_turns"
    if (
        goal.max_tool_calls is not None
        and int(budget["tool_calls"] or 0) >= goal.max_tool_calls
    ):
        return "max_tool_calls"
    if (
        goal.max_cost_usd is not None
        and budget["cost_usd"] is not None
        and float(budget["cost_usd"]) >= goal.max_cost_usd
    ):
        return "max_cost_usd"
    if (
        goal.max_wall_seconds is not None
        and budget["wall_seconds"] is not None
        and float(budget["wall_seconds"]) >= goal.max_wall_seconds
    ):
        return "max_wall_seconds"
    return None


def _missing_required_evidence(
    goal: GoalContract, evaluator_result: GoalEvaluatorResult | None
) -> list[str]:
    if not goal.required_evidence:
        return []
    observed = (
        set(evaluator_result.observed_evidence) if evaluator_result is not None else set()
    )
    return [item for item in goal.required_evidence if item not in observed]


__all__ = ["evaluate_goal_supervision"]
