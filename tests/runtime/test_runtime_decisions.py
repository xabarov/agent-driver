"""Runtime decision contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    AgentRunInput,
    GoalContract,
    GoalEvaluatorResult,
    GoalState,
    RuntimeDecision,
)
from agent_driver.runtime.goal_supervision import evaluate_goal_supervision


def test_runtime_decision_accepts_trace_safe_fields() -> None:
    decision = RuntimeDecision(
        decision_id="dec_1",
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
        kind="retry",
        trigger="finalize",
        action="retry",
        reason="node_contract_no_tool_use_reprompt",
        affected_tools=["lookup_a"],
        redacted_metadata={"policy_decision": "deny"},
    )

    assert decision.model_dump(mode="json")["affected_tools"] == ["lookup_a"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "raw_prompt"),
        ("action", "think_secretly"),
        ("status", "reasoning"),
    ],
)
def test_runtime_decision_rejects_unknown_taxonomy(field: str, value: str) -> None:
    payload = {
        "decision_id": "dec_1",
        "run_id": "run_1",
        "attempt_id": "attempt_1",
        "seq": 1,
        "kind": "retry",
        "trigger": "finalize",
        "action": "retry",
        "reason": "node_contract_no_tool_use_reprompt",
        "status": "applied",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        RuntimeDecision(**payload)


def test_goal_contract_and_state_are_inert_by_default() -> None:
    contract = GoalContract(goal_id="goal_1", objective="finish the run")
    state = GoalState()

    assert contract.scope == "run"
    assert state.status == "inactive"


def test_agent_run_input_accepts_inert_goal_contract() -> None:
    run_input = AgentRunInput(
        input="finish this",
        agent_id="agent",
        graph_preset="single_react",
        goal_contract=GoalContract(goal_id="goal_1", objective="finish this"),
    )

    assert run_input.goal_contract is not None
    assert run_input.goal_contract.goal_id == "goal_1"


def test_goal_supervision_marks_achieved_from_evaluator() -> None:
    goal = GoalContract(goal_id="goal_1", objective="write report")
    state, decision = evaluate_goal_supervision(
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
        goal=goal,
        evaluator_result=GoalEvaluatorResult(
            goal_id="goal_1",
            status="achieved",
            reason="acceptance_criteria_satisfied",
            observed_evidence=["report_artifact"],
        ),
    )

    assert state.status == "achieved"
    assert decision.action == "mark_achieved"
    assert decision.status == "satisfied"


def test_goal_supervision_blocks_on_budget() -> None:
    goal = GoalContract(goal_id="goal_1", objective="write report", max_turns=2)
    state, decision = evaluate_goal_supervision(
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
        goal=goal,
        turn_count=2,
    )

    assert state.status == "blocked"
    assert decision.action == "mark_blocked"
    assert decision.reason == "goal_budget_exceeded:max_turns"


def test_goal_supervision_continues_for_missing_required_evidence() -> None:
    goal = GoalContract(
        goal_id="goal_1",
        objective="write sourced report",
        required_evidence=["source_evidence"],
    )
    state, decision = evaluate_goal_supervision(
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
        goal=goal,
        evaluator_result=GoalEvaluatorResult(
            goal_id="goal_1",
            status="continue",
            reason="needs_sources",
            observed_evidence=[],
        ),
    )

    assert state.status == "active"
    assert state.continuation_instruction == "continue"
    assert decision.action == "continue"
    assert decision.required_evidence == ["source_evidence"]


def test_goal_supervision_blocks_from_evaluator() -> None:
    goal = GoalContract(goal_id="goal_1", objective="write report")
    state, decision = evaluate_goal_supervision(
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
        goal=goal,
        evaluator_result=GoalEvaluatorResult(
            goal_id="goal_1",
            status="blocked",
            reason="approval_required",
            missing_evidence=["user_approval"],
        ),
    )

    assert state.status == "blocked"
    assert decision.action == "mark_blocked"
    assert decision.required_evidence == ["user_approval"]
