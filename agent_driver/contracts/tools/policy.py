"""Tool policy input/output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import ToolPolicyDecision, ToolPolicyMode, ToolRisk
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_positive_int,
)

# Planning/management tools that orchestrate a run rather than execute domain
# work. When a scoped node restricts ``allowed_tools`` to real executable tools,
# a model may still emit one of these out-of-schema; the runtime treats that as a
# distinct, recoverable denial class (``disallowed_management_tool``) instead of a
# generic policy denial. Kept here so both the executor (denial classification)
# and the runtime (recovery / tool-use progress) share one source of truth.
# ``exit_plan_mode_v2`` is the canonical registered approval-exit tool; the bare
# ``exit_plan_mode`` is its legacy v1 name, still accepted as an alias elsewhere
# (see ``runtime.planning_check.EXIT_PLAN_MODE_TOOL_NAMES``). Both are listed so a
# model emitting either in a scoped node lands in the same recovery class. This
# set is duplicated as a literal rather than imported from ``planning_check`` to
# avoid a contracts -> runtime import cycle.
MANAGEMENT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "todo_write",
        "planning_state_update",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode_v2",
        "exit_plan_mode",
    }
)


class ToolPolicyInput(ContractModel):
    """Per-run tool policy input passed by the calling application."""

    mode: ToolPolicyMode = ToolPolicyMode.ALLOW_TOOLS
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    max_tool_calls: int | None = None
    approval_required_for_risk: ToolRisk | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_tool_calls")
    @classmethod
    def validate_max_tool_calls(cls, value: int | None) -> int | None:
        """Validate positive max tool calls when provided."""
        return ensure_positive_int(value, field_name="max_tool_calls")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class ToolPolicyOutcome(ContractModel):
    """Policy engine output for one tool call."""

    decision: ToolPolicyDecision
    reason: str
    interrupt_reason: str | None = None
    # Host-provided heading for the approval interrupt (e.g. a ``ToolGateAsk``
    # ``title``). When None the interrupt builder falls back to its default
    # ``"Approval required for '<tool>'"`` heading — lets a host with a localised
    # UI override the (English) default. See ``policy_interrupt``.
    interrupt_title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_outcome_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure policy outcome metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="policy outcome metadata")


__all__ = ["MANAGEMENT_TOOL_NAMES", "ToolPolicyInput", "ToolPolicyOutcome"]
