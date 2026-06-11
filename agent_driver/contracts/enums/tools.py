"""Tool governance and execution enums."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class ToolRisk(StrEnum):
    """Risk level assigned to a tool invocation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffectClass(StrEnum):
    """Side-effect profile of a tool."""

    NONE = "none"
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"
    EXTERNAL_ACTION = "external_action"


class ApprovalMode(StrEnum):
    """Policy mode describing when human approval is required."""

    NEVER = "never"
    ON_POLICY_MATCH = "on_policy_match"
    ALWAYS = "always"
    STEP_BY_STEP = "step_by_step"


class InterruptReason(StrEnum):
    """Reason for pausing the run and requesting input."""

    APPROVAL_REQUIRED = "approval_required"
    PLAN_APPROVAL_REQUIRED = "plan_approval_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    GUARDRAIL_REVIEW = "guardrail_review"
    TOOL_ARGS_REVIEW = "tool_args_review"
    STATE_REVIEW = "state_review"
    MANUAL_PAUSE = "manual_pause"


class ResumeAction(StrEnum):
    """Allowed resume actions for a pending interrupt."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    CLARIFY = "clarify"
    PATCH_STATE = "patch_state"
    CANCEL = "cancel"


class ToolTraceStatus(StrEnum):
    """Status progression of a tool trace row."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ToolPolicyMode(StrEnum):
    """Per-run tool policy mode."""

    ALLOW_TOOLS = "allow_tools"
    NO_TOOLS = "no_tools"
    CLARIFY = "clarify"
    APPROVAL_REQUIRED = "approval_required"


class ToolPolicyDecision(StrEnum):
    """Structured policy decision for one planned tool call."""

    ALLOW = "allow"
    DENY = "deny"
    INTERRUPT = "interrupt"


class GuardrailDecision(StrEnum):
    """Decision emitted by guardrail pipeline hook."""

    ALLOW = "allow"
    SANITIZE = "sanitize"
    BLOCK = "block"


__all__ = [
    "ApprovalMode",
    "GuardrailDecision",
    "InterruptReason",
    "ResumeAction",
    "SideEffectClass",
    "ToolPolicyDecision",
    "ToolPolicyMode",
    "ToolRisk",
    "ToolTraceStatus",
]
