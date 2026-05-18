"""Top-level runtime input/output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.artifacts import ArtifactRef, RunWarning, TraceRef
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.interrupts import InterruptRequest, ResumeCommand
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.subagents import SubagentRun
from agent_driver.contracts.tools import ToolPolicyInput, ToolTrace
from agent_driver.contracts.usage import UsageSummary
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_positive_float,
    ensure_positive_int,
)


class AgentRunInput(ContractModel):
    """App-facing request to start or continue a run."""

    input: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    thread_id: str | None = None
    run_id: str | None = None
    resume: ResumeCommand | None = None
    agent_id: str
    graph_preset: str
    model_role: str = "default"
    tool_policy: ToolPolicyInput = Field(default_factory=ToolPolicyInput)
    deadline_seconds: float | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("deadline_seconds")
    @classmethod
    def validate_deadline(cls, value: float | None) -> float | None:
        """Validate positive deadline seconds when provided."""
        return ensure_positive_float(value, field_name="deadline_seconds")

    @field_validator("max_steps", "max_tool_calls")
    @classmethod
    def validate_positive_optional_ints(cls, value: int | None) -> int | None:
        """Validate positive numeric run limits."""
        return ensure_positive_int(value, field_name="run limit")

    @field_validator("app_metadata")
    @classmethod
    def validate_app_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="app_metadata")

    @model_validator(mode="after")
    def validate_input_presence(self) -> "AgentRunInput":
        """Require user input, message list, or resume command."""
        has_input = bool((self.input or "").strip())
        has_messages = len(self.messages) > 0
        has_resume = self.resume is not None
        if not (has_input or has_messages or has_resume):
            raise ValueError("one of input/messages/resume must be provided")
        return self


class AgentRunOutput(ContractModel):
    """Normalized output envelope for sync and streamed runs."""

    run_id: str
    attempt_id: str
    thread_id: str | None = None
    status: RunStatus
    answer: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    subagent_runs: list[SubagentRun] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    usage: UsageSummary | None = None
    warnings: list[RunWarning] = Field(default_factory=list)
    trace: TraceRef | None = None
    checkpoint: CheckpointRef | None = None
    interrupt: InterruptRequest | None = None
    memory_audit: dict[str, Any] | None = None
    terminal_reason: TerminalReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("memory_audit")
    @classmethod
    def validate_memory_audit(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure optional memory audit is JSON-compatible."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="memory_audit")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "AgentRunOutput":
        """Enforce pause/terminal invariants and terminal event presence."""
        terminal_statuses = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        if self.status == RunStatus.PAUSED and self.interrupt is None:
            raise ValueError("paused outputs require interrupt")
        if self.status in terminal_statuses and self.terminal_reason is None:
            raise ValueError("terminal outputs require terminal_reason")
        if self.status in terminal_statuses:
            event_types = {event.type.value for event in self.events}
            if not (
                "run_completed" in event_types
                or "run_failed" in event_types
                or "run_cancelled" in event_types
            ):
                raise ValueError("terminal outputs require terminal runtime event")
        return self
