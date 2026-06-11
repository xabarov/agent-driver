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


# Edit-family tools whose tool calls carry a file diff (old/new) for the editor.
_EDIT_TOOLS = {"file_write", "file_edit", "file_patch"}


def _file_edit_payload(result: Any) -> tuple[str, str, str | None] | None:
    """Extract ``(path, new_text, old_text)`` from a tool result, or ``None``.

    Reads the structured output the edit tools emit (``preview.before/after`` +
    ``path``); returns ``None`` when the shape isn't an editable file change.
    """
    if not isinstance(result, dict):
        return None
    structured = result.get("structured_output")
    if not isinstance(structured, dict):
        return None
    path = structured.get("path")
    preview = structured.get("preview")
    if not isinstance(path, str) or not isinstance(preview, dict):
        return None
    after = preview.get("after")
    before = preview.get("before")
    if not isinstance(after, str):
        return None
    return (path, after, before if isinstance(before, str) else None)


def tool_updates_from_trace(output: AgentRunOutput, *, emitted: set[str]) -> list[Any]:
    """Build start+progress tool-call updates from a finished leg's trace.

    ``emitted`` tracks already-reported call ids so a multi-leg prompt does not
    re-announce the same tool call after a resume. Edit-family tools are emitted
    as ACP *edit* tool calls carrying a file diff (old/new) so the editor can
    render the change inline; the structured diff is sourced from the run's
    ``tool_results`` (the trace itself carries only a string summary).
    """
    results = output.metadata.get("tool_results")
    results = results if isinstance(results, list) else []
    updates: list[Any] = []
    for index, trace in enumerate(output.tool_trace or []):
        call_id = trace.tool_call_id or f"{output.run_id}:tool:{index}"
        if call_id in emitted:
            continue
        emitted.add(call_id)
        status = _TOOL_STATUS.get(_enum_value(trace.status), "completed")
        result = results[index] if index < len(results) else None
        edit = _file_edit_payload(result) if trace.tool_name in _EDIT_TOOLS else None
        if edit is not None:
            path, new_text, old_text = edit
            diff = acp.tool_diff_content(path, new_text, old_text)
            updates.append(
                acp.start_edit_tool_call(call_id, trace.tool_name, path, diff)
            )
            # Re-send the diff in the update's content array — editors render the
            # inline diff from there (start_edit_tool_call only sets raw_input).
            updates.append(acp.update_tool_call(call_id, status=status, content=[diff]))
            continue
        kind = tool_kind_for(trace.tool_name)
        updates.append(acp.start_tool_call(call_id, trace.tool_name, kind=kind))
        content = None
        if trace.result_summary:
            content = [acp.tool_content(acp.text_block(trace.result_summary))]
        updates.append(acp.update_tool_call(call_id, status=status, content=content))
    return updates


_PLAN_STATUSES = {"pending", "in_progress", "completed"}


def plan_update_from_results(output: AgentRunOutput) -> Any | None:
    """Build an ACP ``plan`` update from the leg's latest ``todo_write`` call.

    The todo list lives in the tool call's ``args`` (``output.tool_results``);
    we map each ``{content, status}`` to a ``PlanEntry``. Returns ``None`` when
    the leg ran no ``todo_write``.
    """
    results = output.metadata.get("tool_results")
    if not isinstance(results, list):
        return None
    todos: list[Any] | None = None
    for result in results:  # take the most recent todo_write in this leg
        if not isinstance(result, dict):
            continue
        call = result.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "todo_write":
            continue
        args = call.get("args")
        if isinstance(args, dict) and isinstance(args.get("todos"), list):
            todos = args["todos"]
    if not todos:
        return None
    entries: list[Any] = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("title")
        if not isinstance(content, str) or not content:
            continue
        status = item.get("status")
        status = status if status in _PLAN_STATUSES else "pending"
        entries.append(acp.plan_entry(content, status=status))
    return acp.update_plan(entries) if entries else None


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


# Slash commands the adapter advertises (available_commands_update) and handles
# in-band in prompt(). Kept small and editor-agnostic.
_SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("clear", "Clear the conversation transcript and start fresh."),
    ("help", "List the available slash commands."),
)


def available_commands_update() -> Any:
    """Build the ACP ``available_commands_update`` advertising slash commands."""
    return schema.AvailableCommandsUpdate(
        available_commands=[
            schema.AvailableCommand(name=name, description=description)
            for name, description in _SLASH_COMMANDS
        ],
        session_update="available_commands_update",
    )


def current_mode_update(mode_id: str) -> Any:
    """Build the ACP ``current_mode_update`` reflecting the active session mode."""
    return schema.CurrentModeUpdate(
        current_mode_id=mode_id, session_update="current_mode_update"
    )


def slash_command_name(text: str | None) -> str | None:
    """Return the command name if ``text`` is exactly a known ``/command``."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    name = stripped[1:].split(maxsplit=1)[0] if len(stripped) > 1 else ""
    known = {name for name, _ in _SLASH_COMMANDS}
    return name if name in known else None


def slash_help_text() -> str:
    """Human-readable list of the advertised slash commands."""
    lines = [f"/{name} — {description}" for name, description in _SLASH_COMMANDS]
    return "Available commands:\n" + "\n".join(lines)


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
