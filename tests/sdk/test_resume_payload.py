"""Tests for HTTP-style payload <-> ResumeCommand normalization."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts.enums import InterruptReason, ResumeAction, ToolRisk
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.sdk import (
    interrupt_to_stream_event,
    resume_command_from_payload,
)

# ---------------------------------------------------------------------------
# resume_command_from_payload — explicit action / choice paths
# ---------------------------------------------------------------------------


def test_explicit_action_string_alias_normalizes() -> None:
    """String alias under 'action' maps to ResumeAction."""
    command = resume_command_from_payload({"interrupt_id": "i_1", "action": "approve"})
    assert command.interrupt_id == "i_1"
    assert command.action is ResumeAction.APPROVE
    assert command.message is None


def test_explicit_action_modify_alias_is_edit() -> None:
    """'modify' is a documented alias for EDIT."""
    command = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "action": "modify",
            "edited_tool_args": {"target": "fixed"},
        }
    )
    assert command.action is ResumeAction.EDIT
    assert command.edited_tool_args == {"target": "fixed"}


def test_unknown_action_string_raises() -> None:
    """Unrecognized action strings raise ValueError."""
    with pytest.raises(ValueError, match="unknown action"):
        resume_command_from_payload({"interrupt_id": "i_1", "action": "fly"})


def test_legacy_choice_integer_maps_to_correct_action() -> None:
    """Legacy 1/2/3 integer choice maps to approve/edit/cancel.

    Choice=2 (EDIT) additionally requires ``edited_tool_args`` or
    ``state_patch`` in the same payload because ResumeCommand enforces
    that invariant; without one the validator raises.
    """
    approve = resume_command_from_payload({"interrupt_id": "i_1", "choice": 1})
    assert approve.action is ResumeAction.APPROVE

    edit = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "choice": 2,
            "edited_tool_args": {"plan": "use only nmap"},
        }
    )
    assert edit.action is ResumeAction.EDIT
    assert edit.edited_tool_args == {"plan": "use only nmap"}

    cancel = resume_command_from_payload({"interrupt_id": "i_1", "choice": 3})
    assert cancel.action is ResumeAction.CANCEL


def test_legacy_choice_edit_without_payload_surfaces_validator_error() -> None:
    """Bare choice=2 without edited_tool_args/state_patch fails fast.

    This is the existing ResumeCommand invariant — the helper does not
    silently fabricate an empty edit payload.
    """
    with pytest.raises(ValueError, match="edit action requires"):
        resume_command_from_payload({"interrupt_id": "i_1", "choice": 2})


def test_unknown_choice_integer_raises() -> None:
    """Choice outside 1..3 is rejected."""
    with pytest.raises(ValueError, match="unknown choice"):
        resume_command_from_payload({"interrupt_id": "i_1", "choice": 99})


# ---------------------------------------------------------------------------
# resume_command_from_payload — opaque resume / answer / value path
# ---------------------------------------------------------------------------


def test_opaque_resume_string_yes_is_approve() -> None:
    """Opaque 'yes' string maps to APPROVE via default rule."""
    command = resume_command_from_payload({"interrupt_id": "i_1", "resume": "yes"})
    assert command.action is ResumeAction.APPROVE


def test_opaque_resume_string_no_is_reject() -> None:
    """Opaque 'no' string maps to REJECT via default rule."""
    command = resume_command_from_payload({"interrupt_id": "i_1", "resume": "no"})
    assert command.action is ResumeAction.REJECT


def test_opaque_resume_boolean_maps_to_action() -> None:
    """True/False booleans map to APPROVE/REJECT."""
    assert (
        resume_command_from_payload({"interrupt_id": "i_1", "resume": True}).action
        is ResumeAction.APPROVE
    )
    assert (
        resume_command_from_payload({"interrupt_id": "i_1", "resume": False}).action
        is ResumeAction.REJECT
    )


def test_opaque_resume_integer_0_or_1_maps_to_action() -> None:
    """0/1 integers map to REJECT/APPROVE."""
    assert (
        resume_command_from_payload({"interrupt_id": "i_1", "resume": 1}).action
        is ResumeAction.APPROVE
    )
    assert (
        resume_command_from_payload({"interrupt_id": "i_1", "resume": 0}).action
        is ResumeAction.REJECT
    )


def test_opaque_resume_dict_is_edit_with_inferred_tool_args() -> None:
    """Dict payload becomes EDIT with edited_tool_args populated."""
    command = resume_command_from_payload(
        {"interrupt_id": "i_1", "resume": {"flag": "--quiet"}}
    )
    assert command.action is ResumeAction.EDIT
    assert command.edited_tool_args == {"flag": "--quiet"}


def test_opaque_resume_free_text_is_clarify_with_message() -> None:
    """Non-alias non-empty string becomes CLARIFY with message preserved."""
    command = resume_command_from_payload(
        {"interrupt_id": "i_1", "resume": "please show me only ports 80,443"}
    )
    assert command.action is ResumeAction.CLARIFY
    assert command.message == "please show me only ports 80,443"


def test_opaque_resume_alias_under_answer_key() -> None:
    """Alias 'answer' is recognized as opaque resume value."""
    command = resume_command_from_payload({"interrupt_id": "i_1", "answer": "approve"})
    assert command.action is ResumeAction.APPROVE


def test_opaque_resume_alias_under_value_key() -> None:
    """Alias 'value' is recognized as opaque resume value."""
    command = resume_command_from_payload({"interrupt_id": "i_1", "value": "no"})
    assert command.action is ResumeAction.REJECT


def test_opaque_resume_list_is_clarify_with_json_message() -> None:
    """Non-empty list becomes CLARIFY with stable JSON message."""
    command = resume_command_from_payload(
        {"interrupt_id": "i_1", "resume": ["opt-a", "opt-b"]}
    )
    assert command.action is ResumeAction.CLARIFY
    assert command.message == json.dumps(["opt-a", "opt-b"])


def test_empty_payload_raises_value_error() -> None:
    """Empty payload without any action signal raises so host returns 400."""
    with pytest.raises(ValueError, match="cannot infer ResumeAction"):
        resume_command_from_payload({"interrupt_id": "i_1"})


def test_custom_value_to_action_overrides_default() -> None:
    """Host can supply a domain-specific resolver."""

    def host_resolver(value):
        if value == "kthx":
            return ResumeAction.APPROVE
        return None

    command = resume_command_from_payload(
        {"interrupt_id": "i_1", "resume": "kthx"},
        value_to_action=host_resolver,
    )
    assert command.action is ResumeAction.APPROVE


# ---------------------------------------------------------------------------
# resume_command_from_payload — interrupt_id resolution and type errors
# ---------------------------------------------------------------------------


def test_explicit_interrupt_id_overrides_default() -> None:
    """interrupt_id in body wins over default_interrupt_id."""
    command = resume_command_from_payload(
        {"interrupt_id": "i_body", "action": "approve"},
        default_interrupt_id="i_default",
    )
    assert command.interrupt_id == "i_body"


def test_default_interrupt_id_used_when_body_missing() -> None:
    """default_interrupt_id is adopted when body lacks one."""
    command = resume_command_from_payload(
        {"action": "approve"},
        default_interrupt_id="i_default",
    )
    assert command.interrupt_id == "i_default"


def test_missing_interrupt_id_without_default_raises() -> None:
    """No interrupt_id in body and no default → ValueError."""
    with pytest.raises(ValueError, match="interrupt_id"):
        resume_command_from_payload({"action": "approve"})


def test_message_field_passes_through() -> None:
    """Explicit message overrides any inferred message."""
    command = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "action": "clarify",
            "message": "narrow to ports 80,443",
        }
    )
    assert command.action is ResumeAction.CLARIFY
    assert command.message == "narrow to ports 80,443"


def test_metadata_field_passes_through() -> None:
    """Pass-through metadata is preserved."""
    command = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "action": "approve",
            "metadata": {"actor": "operator-7", "channel": "slack"},
        }
    )
    assert command.metadata == {"actor": "operator-7", "channel": "slack"}


def test_state_patch_field_passes_through() -> None:
    """state_patch dict is preserved for PATCH_STATE flows."""
    command = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "action": "patch_state",
            "state_patch": {"phase": "review"},
        }
    )
    assert command.action is ResumeAction.PATCH_STATE
    assert command.state_patch == {"phase": "review"}


def test_non_mapping_payload_rejected() -> None:
    """Non-Mapping inputs raise TypeError."""
    with pytest.raises(TypeError, match="Mapping"):
        resume_command_from_payload(["bad", "input"])  # type: ignore[arg-type]


def test_non_string_message_rejected() -> None:
    """Non-string message rejected with TypeError."""
    with pytest.raises(TypeError, match="message"):
        resume_command_from_payload(
            {"interrupt_id": "i_1", "action": "approve", "message": 123}
        )


def test_non_mapping_edited_tool_args_rejected() -> None:
    """Non-Mapping edited_tool_args rejected."""
    with pytest.raises(TypeError, match="edited_tool_args"):
        resume_command_from_payload(
            {
                "interrupt_id": "i_1",
                "action": "edit",
                "edited_tool_args": ["not", "a", "mapping"],
            }
        )


def test_approved_by_field_passes_through() -> None:
    """approved_by actor name is preserved."""
    command = resume_command_from_payload(
        {
            "interrupt_id": "i_1",
            "action": "approve",
            "approved_by": "alice@example.com",
        }
    )
    assert command.approved_by == "alice@example.com"


# ---------------------------------------------------------------------------
# interrupt_to_stream_event
# ---------------------------------------------------------------------------


def _make_interrupt() -> InterruptRequest:
    return InterruptRequest(
        interrupt_id="intr_1",
        run_id="run_1",
        attempt_id="att_1",
        checkpoint_id="ckpt_1",
        reason=InterruptReason.APPROVAL_REQUIRED,
        title="Approve plan",
        description="Approve the proposed tool plan before execution.",
        risk=ToolRisk.MEDIUM,
        proposed_action={
            "tool_name": "nuclei",
            "tool_call_id": "call_1",
            "args": {"target": "https://example.test", "templates": "default"},
        },
        allowed_actions=[
            ResumeAction.APPROVE,
            ResumeAction.REJECT,
            ResumeAction.EDIT,
        ],
        editable_fields=["args.target"],
        expires_at="2026-05-21T12:00:00Z",
        metadata={"plan_id": "plan_42"},
    )


def test_interrupt_to_stream_event_contains_core_fields() -> None:
    """Projection captures all top-level interrupt fields."""
    event = interrupt_to_stream_event(_make_interrupt())
    assert event["type"] == "interrupt_requested"
    assert event["interrupt_id"] == "intr_1"
    assert event["run_id"] == "run_1"
    assert event["attempt_id"] == "att_1"
    assert event["checkpoint_id"] == "ckpt_1"
    assert event["reason"] == "approval_required"
    assert event["title"] == "Approve plan"
    assert event["risk"] == "medium"
    assert event["allowed_actions"] == ["approve", "reject", "edit"]
    assert event["editable_fields"] == ["args.target"]
    assert event["expires_at"] == "2026-05-21T12:00:00Z"
    assert event["proposed_action"]["tool_name"] == "nuclei"
    assert event["metadata"] == {"plan_id": "plan_42"}


def test_interrupt_to_stream_event_includes_approval_payload() -> None:
    """Projection embeds a deterministic ApprovalPayload card."""
    event = interrupt_to_stream_event(_make_interrupt())
    card = event["approval_payload"]
    assert card["interrupt_id"] == "intr_1"
    assert card["tool_name"] == "nuclei"
    assert card["tool_call_id"] == "call_1"
    assert card["allowed_actions"] == ["approve", "reject", "edit"]
    # args_preview should be a truncation of the proposed args.
    assert "https://example.test" in card["args_preview"]


def test_interrupt_to_stream_event_supports_custom_event_type() -> None:
    """Hosts can choose their own event_type label (e.g. plan.proposed)."""
    event = interrupt_to_stream_event(_make_interrupt(), event_type="plan.proposed")
    assert event["type"] == "plan.proposed"


def test_interrupt_to_stream_event_risk_optional() -> None:
    """When interrupt has no risk, projection carries null risk."""
    interrupt = _make_interrupt().model_copy(update={"risk": None})
    event = interrupt_to_stream_event(interrupt)
    assert event["risk"] is None
