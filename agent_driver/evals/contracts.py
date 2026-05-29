"""Phase-5 deterministic evaluation contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
)


class BudgetLimits(ContractModel):
    """Optional budget limits applied by deterministic budget evaluator."""

    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    max_latency_ms: int | None = None

    @field_validator("max_total_tokens", "max_latency_ms")
    @classmethod
    def validate_int_limits(cls, value: int | None) -> int | None:
        """Validate non-negative integer limits."""
        return ensure_non_negative_int(value, field_name="budget integer limit")

    @field_validator("max_cost_usd")
    @classmethod
    def validate_cost_limit(cls, value: float | None) -> float | None:
        """Validate non-negative cost limit."""
        return ensure_non_negative_float(value, field_name="max_cost_usd")


class DatasetCase(ContractModel):
    """One runnable evaluation case for local dataset runner."""

    case_id: str
    description: str
    run_input: AgentRunInput
    expected_status: RunStatus | None = None
    expected_terminal_reason: TerminalReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure case metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="dataset case metadata")


class EvaluatorResult(ContractModel):
    """Result emitted by one deterministic evaluator."""

    evaluator: str
    passed: bool
    score: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure evaluator details are JSON-compatible."""
        return ensure_json_serializable(value, field_name="evaluator details")


class CaseEvaluation(ContractModel):
    """Full result for one dataset case execution."""

    case_id: str
    output: AgentRunOutput
    evaluations: list[EvaluatorResult] = Field(default_factory=list)
    passed: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure case evaluation metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="case evaluation metadata")


class EvalReport(ContractModel):
    """Dataset-level deterministic evaluation report."""

    report_id: str
    candidate_id: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    cases: list[CaseEvaluation] = Field(default_factory=list)
    passed_cases: int = 0
    failed_cases: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure report metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="report metadata")


class ReportComparison(ContractModel):
    """Comparison summary between baseline and candidate reports."""

    baseline_report_id: str
    candidate_report_id: str
    regressions: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure comparison details are JSON-compatible."""
        return ensure_json_serializable(value, field_name="comparison details")


def _status_bucket(case: CaseEvaluation) -> dict[str, Any]:
    """Project one case to comparison-relevant status/trajectory facets."""
    return {
        "status": case.output.status.value,
        "terminal_reason": (
            case.output.terminal_reason.value if case.output.terminal_reason else None
        ),
        "trajectory": [event.type.value for event in case.output.events],
        "tool_statuses": [trace.status.value for trace in case.output.tool_trace],
        "total_tokens": case.output.usage.total_tokens if case.output.usage else 0,
        "cost_usd_estimate": (
            case.output.usage.cost_usd_estimate if case.output.usage else None
        ),
    }
