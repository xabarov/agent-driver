"""Contracts for batch trajectory generation.

The batch runner runs an agent over a dataset of prompts and records one
:class:`Trajectory` per item — the conversation, tool calls and usage — for
training data, replay or analysis. This is distinct from ``evals`` (which
*scores* runs against evaluators); here we just capture what happened and
aggregate tool/usage stats.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.contracts.validation import ensure_json_serializable


class BatchItem(ContractModel):
    """One prompt to run in a batch."""

    item_id: str
    input: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-serializable."""
        return ensure_json_serializable(value, field_name="batch item metadata")


class Trajectory(ContractModel):
    """One recorded run: the conversation, tool calls and usage.

    ``run_index`` distinguishes repeated runs of the same item (N-run
    reliability); ``cost_usd`` / ``latency_ms`` carry per-task economics for
    aggregation (median + percentile) — populated by the runner since cost
    estimation lives in the observability layer, not the contract.
    """

    item_id: str
    run_id: str
    status: str
    answer: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, str]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    run_index: int = 0
    cost_usd: float | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_output(
        item_id: str,
        output: AgentRunOutput,
        *,
        metadata: dict[str, Any] | None = None,
        run_index: int = 0,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
    ) -> "Trajectory":
        """Build a trajectory from a completed run's output."""
        usage = output.usage
        return Trajectory(
            item_id=item_id,
            run_id=output.run_id,
            status=output.status.value,
            answer=output.answer,
            messages=[
                {
                    "role": getattr(m.role, "value", str(m.role)),
                    "content": m.content,
                }
                for m in output.messages
            ],
            tool_calls=[
                {"tool_name": t.tool_name, "status": t.status.value}
                for t in output.tool_trace
            ],
            usage={
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            run_index=run_index,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )

    @staticmethod
    def from_error(
        item_id: str,
        run_id: str,
        error: str,
        *,
        metadata: dict[str, Any] | None = None,
        run_index: int = 0,
        latency_ms: float | None = None,
    ) -> "Trajectory":
        """Build a trajectory for an item whose run raised."""
        return Trajectory(
            item_id=item_id,
            run_id=run_id,
            status="error",
            error=error,
            run_index=run_index,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )


class BatchReport(ContractModel):
    """Aggregate over the trajectories produced in one batch run."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped_resumed: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    tool_usage: dict[str, int] = Field(default_factory=dict)
    total_tokens: int = 0

    @staticmethod
    def from_trajectories(
        trajectories: list[Trajectory], *, skipped_resumed: int = 0
    ) -> "BatchReport":
        """Aggregate counts, status histogram, tool usage and tokens."""
        by_status: Counter[str] = Counter()
        tool_usage: Counter[str] = Counter()
        total_tokens = 0
        for traj in trajectories:
            by_status[traj.status] += 1
            for call in traj.tool_calls:
                tool_usage[call["tool_name"]] += 1
            total_tokens += traj.usage.get("total_tokens", 0)
        return BatchReport(
            total=len(trajectories),
            completed=by_status.get("completed", 0),
            failed=sum(
                count for status, count in by_status.items() if status != "completed"
            ),
            skipped_resumed=skipped_resumed,
            by_status=dict(by_status),
            tool_usage=dict(tool_usage),
            total_tokens=total_tokens,
        )


__all__ = ["BatchItem", "BatchReport", "Trajectory"]
