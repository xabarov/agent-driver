"""Pure translators between runtime vocabulary and ACP messages.

These functions own the entire mapping from our ``RunStreamEvent`` /
``InterruptRequest`` / ``AgentRunOutput`` vocabulary onto ACP session updates,
permission options, and stop reasons. Keeping them free of I/O makes the
adapter trivially unit-testable.
"""

from __future__ import annotations

from typing import Any

import acp
from acp import schema

from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.enums.runtime import RuntimeEventType
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.contracts.stream import RunStreamEvent

# Our built-in tool names -> ACP ToolKind literals (read/edit/execute/...).
_TOOL_KIND: dict[str, str] = {
    "read_file": "read",
    "glob_search": "search",
    "grep_search": "search",
    "file_write": "edit",
    "file_edit": "edit",
    "file_patch": "edit",
    "bash": "execute",
    "python": "execute",
    "web_fetch": "fetch",
    "web_search": "fetch",
}

# ToolTraceStatus value -> ACP ToolCallStatus literal.
_TOOL_STATUS: dict[str, str] = {
    "started": "in_progress",
    "completed": "completed",
    "failed": "failed",
    "denied": "failed",
    "timed_out": "failed",
}

# ResumeAction -> ACP PermissionOptionKind literal.
_OPTION_KIND: dict[ResumeAction, str] = {
    ResumeAction.APPROVE: "allow_once",
    ResumeAction.REJECT: "reject_once",
}


def tool_kind_for(tool_name: str) -> str:
    """Map a runtime tool name to an ACP tool kind."""
    return _TOOL_KIND.get(tool_name, "other")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _delta_text(data: dict[str, Any]) -> str | None:
    for key in ("delta_text", "delta", "text"):
        candidate = data.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def text_update_for(event: RunStreamEvent) -> Any | None:
    """Map a streamed token/reasoning delta to an ACP update, or ``None``."""
    if event.event == RuntimeEventType.TOKEN_DELTA.value:
        text = _delta_text(event.data)
        return acp.update_agent_message_text(text) if text else None
    if event.event == RuntimeEventType.REASONING_DELTA.value:
        text = _delta_text(event.data)
        return acp.update_agent_thought_text(text) if text else None
    return None


def tool_updates_from_trace(output: AgentRunOutput, *, emitted: set[str]) -> list[Any]:
    """Build start+progress tool-call updates from a finished leg's trace.

    ``emitted`` tracks already-reported call ids so a multi-leg prompt does not
    re-announce the same tool call after a resume.
    """
    updates: list[Any] = []
    for index, trace in enumerate(output.tool_trace or []):
        call_id = trace.tool_call_id or f"{output.run_id}:tool:{index}"
        if call_id in emitted:
            continue
        emitted.add(call_id)
        kind = tool_kind_for(trace.tool_name)
        updates.append(acp.start_tool_call(call_id, trace.tool_name, kind=kind))
        status = _TOOL_STATUS.get(_enum_value(trace.status), "completed")
        content = None
        if trace.result_summary:
            content = [acp.tool_content(acp.text_block(trace.result_summary))]
        updates.append(acp.update_tool_call(call_id, status=status, content=content))
    return updates


def permission_options_for(
    interrupt: InterruptRequest,
) -> list[schema.PermissionOption]:
    """Derive ACP permission options from an interrupt's allowed actions."""
    options: list[schema.PermissionOption] = []
    for action in interrupt.allowed_actions:
        kind = _OPTION_KIND.get(action)
        if kind is None:
            continue
        options.append(
            schema.PermissionOption(
                option_id=action.value, name=action.value.title(), kind=kind
            )
        )
    if not options:
        options = [
            schema.PermissionOption(
                option_id=ResumeAction.APPROVE.value, name="Approve", kind="allow_once"
            ),
            schema.PermissionOption(
                option_id=ResumeAction.REJECT.value, name="Reject", kind="reject_once"
            ),
        ]
    return options


def permission_tool_call(interrupt: InterruptRequest) -> schema.ToolCallUpdate:
    """Build the ToolCallUpdate that frames a permission request in the client."""
    return schema.ToolCallUpdate(
        tool_call_id=interrupt.interrupt_id,
        title=interrupt.title or "Tool approval required",
        status="pending",
    )


def resume_action_from_outcome(outcome: Any) -> ResumeAction:
    """Map an ACP permission outcome back to a runtime resume action."""
    option_id = getattr(outcome, "option_id", None)
    if option_id:
        try:
            return ResumeAction(option_id)
        except ValueError:
            pass
    if "allow" in type(outcome).__name__.lower():
        return ResumeAction.APPROVE
    return ResumeAction.REJECT


# TerminalReason value -> ACP StopReason literal. Reasons absent here fall back
# to the status-based mapping below. ACP StopReason values:
# end_turn / max_tokens / max_turn_requests / refusal / cancelled.
_STOP_REASON_BY_TERMINAL: dict[str, str] = {
    "final_answer": "end_turn",
    "cancelled_by_user": "cancelled",
    "max_steps_exceeded": "max_turn_requests",
    "budget_exceeded": "max_turn_requests",
    "deadline_exceeded": "max_turn_requests",
    # Approval/policy/guardrail blocks and runtime/model errors are not a normal
    # turn end: ACP has no error variant, so "refusal" is the honest signal.
    "approval_rejected": "refusal",
    "tool_policy_denied": "refusal",
    "guardrail_blocked": "refusal",
    "runtime_error": "refusal",
    "model_error": "refusal",
    "provider_protocol": "refusal",
    "checkpoint_error": "refusal",
}


# ACP session modes -> our permission posture. "default" leaves the agent's
# construction-time gate untouched; the others override it per session.
_SESSION_MODES: tuple[tuple[str, str, str], ...] = (
    ("default", "Default", "Use the agent's configured permission policy."),
    ("yolo", "Yolo", "Allow every tool call without asking."),
    ("standard", "Standard", "Ask before dangerous tool calls."),
    ("strict", "Strict", "Ask before dangerous and cautious tool calls."),
)


def session_mode_state(current_mode_id: str = "default") -> Any:
    """Build the ACP ``SessionModeState`` advertising the permission modes."""
    available = [
        schema.SessionMode(id=mode_id, name=name, description=description)
        for mode_id, name, description in _SESSION_MODES
    ]
    return schema.SessionModeState(
        available_modes=available, current_mode_id=current_mode_id
    )


def is_known_mode(mode_id: str) -> bool:
    """Whether ``mode_id`` is one of the advertised session modes."""
    return any(mode_id == known for known, _, _ in _SESSION_MODES)


def gate_for_mode(mode_id: str) -> Any | None:
    """Map a session mode to a runtime ``ToolGate`` override (or ``None``).

    ``None`` means "do not override" — the run uses the agent's default gate.
    """
    # Imported lazily so the adapter's import graph does not pull permissions
    # unless mode switching is actually used.
    from agent_driver.permissions import (
        PermissionMode,
        PermissionPolicy,
        build_permission_gate,
    )
    from agent_driver.runtime.tool_gate import ToolGateAllow

    if mode_id == "yolo":

        async def _allow_all(_ctx: Any) -> Any:
            return ToolGateAllow(reason="acp session mode: yolo")

        return _allow_all
    if mode_id in ("standard", "strict"):
        return build_permission_gate(PermissionPolicy(mode=PermissionMode(mode_id)))
    return None  # "default" or unknown -> use the agent's default gate


def history_updates(messages: list[Any]) -> list[Any]:
    """Translate a transcript of messages into ACP replay updates.

    User messages become ``update_user_message_text`` and assistant messages
    ``update_agent_message_text``; other roles (tool/system) are skipped.
    Accepts any object exposing ``role`` and ``content`` (e.g. ``ChatMessage``).
    """
    updates: list[Any] = []
    for message in messages:
        role = _enum_value(getattr(message, "role", "")).lower()
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content:
            continue
        if role == "user":
            updates.append(acp.update_user_message_text(content))
        elif role == "assistant":
            updates.append(acp.update_agent_message_text(content))
    return updates


def stop_reason_for(output: AgentRunOutput) -> str:
    """Map a terminal run to an ACP stop reason literal.

    Prefers the run's ``terminal_reason`` (most specific), then falls back to
    the coarse status: a clean completion is ``end_turn``; a cancel is
    ``cancelled``; anything else terminal (a failed/rejected run) is a
    ``refusal`` — ACP has no error stop reason, so a non-normal end is surfaced
    as a refusal rather than a misleading ``end_turn``.
    """
    terminal = getattr(output.terminal_reason, "value", output.terminal_reason)
    if isinstance(terminal, str):
        mapped = _STOP_REASON_BY_TERMINAL.get(terminal)
        if mapped is not None:
            return mapped
    if output.status == RunStatus.CANCELLED:
        return "cancelled"
    if output.status == RunStatus.COMPLETED:
        return "end_turn"
    return "refusal"
