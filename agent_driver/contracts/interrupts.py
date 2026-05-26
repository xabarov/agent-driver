"""Interrupt and resume contracts."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import InterruptReason, ResumeAction, ToolRisk
from agent_driver.contracts.validation import ensure_json_serializable


# Phase 11 H13 — prompt-based permissions / "allowed prompts".
#
# When the operator approves an interrupt at plan-exit (or any other
# approval point), they may approve a *category* in addition to the
# specific call. Subsequent tool calls whose shape matches the category
# auto-approve without raising another interrupt — eliminating the
# repetitive prompt-fatigue around predictable bulk operations
# ("run tests", "modify build files", "git commit / push", etc.).
#
# Match logic:
# * tool name must equal ``tool_name`` (exact).
# * each ``arg_pattern`` is a regex; ALL must match the corresponding
#   argument's string form. A pattern entry whose argument is absent
#   is treated as a non-match (cautious-by-default).
# * Empty ``arg_patterns`` means "match any args for this tool".
#
# Categories are scoped to the current run (stored in run metadata),
# not persistent across runs — preserves the "approve once per run"
# UX without leaking trust across sessions.


class AllowedPromptPattern(ContractModel):
    """One argument-level regex inside an :class:`AllowedPrompt`."""

    arg_name: str
    regex: str

    @field_validator("regex")
    @classmethod
    def validate_regex_compiles(cls, value: str) -> str:
        """Ensure the regex compiles so matcher errors don't surface at
        approval time."""
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"AllowedPromptPattern.regex invalid: {exc}") from exc
        return value


class AllowedPrompt(ContractModel):
    """Operator-approved semantic category of tool calls.

    Sent FROM the runtime AS part of an :class:`InterruptRequest`
    (proposed categories) and FROM the host AS part of the
    :class:`ResumeCommand` (operator-approved subset).
    """

    category_id: str
    description: str
    tool_name: str
    arg_patterns: list[AllowedPromptPattern] = Field(default_factory=list)
    expires_at: str | None = None

    @field_validator("category_id")
    @classmethod
    def validate_category_id(cls, value: str) -> str:
        """Ensure stable id with no whitespace (used as dict key)."""
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            raise ValueError("category_id must be non-empty and contain no whitespace")
        return cleaned


def matches_allowed_prompt(
    *,
    tool_name: str,
    args: dict[str, Any],
    allowed: AllowedPrompt,
) -> bool:
    """Return True when ``(tool_name, args)`` satisfies an approved prompt.

    Cautious-by-default: every pattern must match. Patterns reference
    args by name; missing args → match fails. Empty ``arg_patterns``
    means "any args" (use carefully — implies blanket trust for the
    tool).
    """
    if tool_name != allowed.tool_name:
        return False
    if not allowed.arg_patterns:
        return True
    for pattern in allowed.arg_patterns:
        if pattern.arg_name not in args:
            return False
        value = args[pattern.arg_name]
        if not isinstance(value, str):
            # Coerce non-strings to JSON for regex match (covers ints,
            # lists, dicts the model might emit as values).
            try:
                value = json.dumps(value, ensure_ascii=True, sort_keys=True)
            except (TypeError, ValueError):
                return False
        if not re.search(pattern.regex, value):
            return False
    return True


def find_matching_prompt(
    *,
    tool_name: str,
    args: dict[str, Any],
    approved: list[AllowedPrompt],
) -> AllowedPrompt | None:
    """Return the FIRST approved prompt that matches, else None.

    First-match semantic mirrors permission rule ordering — categories
    earlier in the operator's approval list win, allowing them to be
    listed in priority order ("specific" categories before "blanket").
    """
    for candidate in approved:
        if matches_allowed_prompt(tool_name=tool_name, args=args, allowed=candidate):
            return candidate
    return None


class ResumeCommand(ContractModel):
    """Command payload used to continue a paused run."""

    interrupt_id: str
    action: ResumeAction
    message: str | None = None
    edited_tool_args: dict[str, Any] | None = None
    state_patch: dict[str, Any] | None = None
    approved_by: str | None = None
    created_at: str | None = None
    # Phase 11 H13 — categories the operator approves alongside the
    # specific call. These are scoped to the current run (runtime
    # stores them in run metadata so subsequent policy evaluation can
    # consult them).
    approved_prompts: list[AllowedPrompt] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @field_validator("edited_tool_args", "state_patch")
    @classmethod
    def validate_optional_json_payload(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Validate optional edit/patch payload shape."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="resume payload")

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ResumeCommand":
        """Enforce action-specific payload invariants."""
        if self.action == ResumeAction.EDIT and not (
            self.edited_tool_args or self.state_patch
        ):
            raise ValueError("edit action requires edited_tool_args or state_patch")
        if self.action == ResumeAction.CLARIFY and not (self.message or "").strip():
            raise ValueError("clarify action requires message")
        if (
            self.action in {ResumeAction.APPROVE, ResumeAction.REJECT}
            and self.state_patch
        ):
            raise ValueError("approve/reject actions cannot mutate state_patch")
        return self


class InterruptRequest(ContractModel):
    """Persisted pause request for human review or clarification."""

    interrupt_id: str
    run_id: str
    attempt_id: str
    checkpoint_id: str
    reason: InterruptReason
    title: str
    description: str
    risk: ToolRisk | None = None
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[ResumeAction] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)
    # Phase 11 H13 — proposed categories the runtime suggests the
    # operator approve to avoid repeated prompts. The host UI can
    # render these as checkboxes alongside the approve button. The
    # operator's ``ResumeCommand.approved_prompts`` carries the
    # subset they accepted.
    proposed_prompts: list[AllowedPrompt] = Field(default_factory=list)
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("proposed_action", "metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure proposed action and metadata are JSON-compatible."""
        return ensure_json_serializable(value, field_name="interrupt payload")

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "InterruptRequest":
        """Require at least one allowed resume action."""
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        return self


class ApprovalPayload(ContractModel):
    """UI-facing approval card payload derived from interrupt state."""

    interrupt_id: str
    reason: InterruptReason
    title: str
    description: str
    risk: ToolRisk | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    args_preview: str | None = None
    allowed_actions: list[ResumeAction] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "ApprovalPayload":
        """Require at least one allowed action for UI review cards."""
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        return self

    @classmethod
    def from_interrupt(
        cls, interrupt: InterruptRequest, *, args_preview_chars: int = 280
    ) -> "ApprovalPayload":
        """Create deterministic approval payload from interrupt request."""
        proposed_action = interrupt.proposed_action
        tool_name = (
            proposed_action.get("tool_name")
            if isinstance(proposed_action.get("tool_name"), str)
            else None
        )
        tool_call_id = (
            proposed_action.get("tool_call_id")
            if isinstance(proposed_action.get("tool_call_id"), str)
            else None
        )
        raw_args = proposed_action.get("args")
        args_preview: str | None = None
        if raw_args is not None:
            rendered = json.dumps(raw_args, ensure_ascii=True, sort_keys=True)
            args_preview = (
                rendered[:args_preview_chars].rstrip() + "..."
                if len(rendered) > args_preview_chars
                else rendered
            )
        return cls(
            interrupt_id=interrupt.interrupt_id,
            reason=interrupt.reason,
            title=interrupt.title,
            description=interrupt.description,
            risk=interrupt.risk,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args_preview=args_preview,
            allowed_actions=list(interrupt.allowed_actions),
            editable_fields=list(interrupt.editable_fields),
            metadata=dict(interrupt.metadata),
        )
