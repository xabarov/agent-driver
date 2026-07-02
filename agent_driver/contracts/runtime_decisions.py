"""Trace-safe runtime decision contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable


_VALID_KINDS = frozenset(
    {
        "goal",
        "planning",
        "tool_guardrail",
        "steering",
        "evidence",
        "retry",
        "force_final",
        "approval",
        "scope",
        "budget",
    }
)
_VALID_ACTIONS = frozenset(
    {
        "continue",
        "retry",
        "force_final",
        "interrupt",
        "block",
        "ask_user",
        "mark_achieved",
        "mark_blocked",
        "warn",
    }
)
_VALID_STATUSES = frozenset({"proposed", "applied", "skipped", "satisfied", "failed"})
_VALID_EVALUATOR_STATUSES = frozenset({"unknown", "continue", "achieved", "blocked", "failed"})


class GoalContract(ContractModel):
    """Optional bounded-goal contract supplied by a host application."""

    goal_id: str
    objective: str
    scope: str = "run"
    acceptance_criteria: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    allowed_retries: int | None = None
    max_turns: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    max_wall_seconds: float | None = None
    requires_user_approval: bool = False
    evaluator: str | None = None
    blocked_conditions: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class GoalState(ContractModel):
    """Trace-safe snapshot of bounded-goal supervision state."""

    goal_id: str | None = None
    status: str = "inactive"
    turn_count: int = 0
    last_decision_id: str | None = None
    last_evaluator_result: str | None = None
    continuation_instruction: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)

    @field_validator("budget")
    @classmethod
    def validate_budget(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="goal state budget")


class GoalEvaluatorResult(ContractModel):
    """Trace-safe result from a deterministic or host-provided goal evaluator."""

    goal_id: str
    status: str = "unknown"
    reason: str = "not_evaluated"
    observed_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _VALID_EVALUATOR_STATUSES:
            raise ValueError(f"unsupported goal evaluator status: {value}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="goal evaluator metadata")


class RuntimeDecision(ContractModel):
    """Compact record for one bounded, trace-safe harness decision."""

    decision_id: str
    run_id: str
    attempt_id: str
    seq: int
    kind: str
    trigger: str
    action: str
    reason: str
    status: str = "applied"
    goal_id: str | None = None
    policy_id: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    affected_tools: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    observed_evidence: list[str] = Field(default_factory=list)
    product_tags: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        if value < 1:
            raise ValueError("seq must be >= 1")
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value not in _VALID_KINDS:
            raise ValueError(f"unsupported runtime decision kind: {value}")
        return value

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in _VALID_ACTIONS:
            raise ValueError(f"unsupported runtime decision action: {value}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _VALID_STATUSES:
            raise ValueError(f"unsupported runtime decision status: {value}")
        return value

    @field_validator("budget", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="runtime decision metadata")


__all__ = ["GoalContract", "GoalEvaluatorResult", "GoalState", "RuntimeDecision"]
