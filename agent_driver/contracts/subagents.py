"""Subagent contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.artifacts import ArtifactRef
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    ParentStateWriteMode,
    SubagentExecutionMode,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.usage import UsageSummary
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
    ensure_positive_int,
)


class MergeProvenance(ContractModel):
    """Describes how child outputs were merged into parent state."""

    strategy: str
    source_kind: str
    carried_keys: list[str] = Field(default_factory=list)
    parent_state_write: ParentStateWriteMode = ParentStateWriteMode.BOUNDED_APPEND_ONLY
    evidence_origin: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class SubagentRun(ContractModel):
    """Canonical child run record for observability and audit."""

    subagent_run_id: str
    parent_run_id: str
    parent_attempt_id: str
    parent_checkpoint_id: str | None = None
    child_run_id: str | None = None
    task_id: str
    task_type: str
    description: str
    execution_mode: SubagentExecutionMode = SubagentExecutionMode.SYNC
    fanout_slot: int = 1
    status: SubagentStatus
    terminal_state: SubagentTerminalState | None = None
    latency_ms: int | None = None
    tokens: UsageSummary | None = None
    cost_usd_estimate: float | None = None
    failure_code: str | None = None
    output_pointer: ArtifactRef | None = None
    merge_provenance: MergeProvenance | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fanout_slot")
    @classmethod
    def validate_fanout_slot(cls, value: int) -> int:
        """Validate positive fanout slot index."""
        return int(ensure_positive_int(value, field_name="fanout_slot"))

    @field_validator("latency_ms")
    @classmethod
    def validate_latency(cls, value: int | None) -> int | None:
        """Validate non-negative child latency."""
        return ensure_non_negative_int(value, field_name="latency_ms")

    @field_validator("cost_usd_estimate")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        """Validate non-negative cost estimate."""
        return ensure_non_negative_float(value, field_name="cost_usd_estimate")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @model_validator(mode="after")
    def validate_terminal_invariants(self) -> "SubagentRun":
        """Require terminal state for terminal statuses and merge evidence for completion."""
        terminal_statuses = {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.CANCELLED,
            SubagentStatus.TIMED_OUT,
        }
        if self.status in terminal_statuses and self.terminal_state is None:
            raise ValueError("terminal_state is required for terminal subagent status")
        if self.status == SubagentStatus.COMPLETED and not (
            self.output_pointer or self.merge_provenance
        ):
            raise ValueError(
                "completed subagent rows should include output_pointer or merge_provenance"
            )
        return self
