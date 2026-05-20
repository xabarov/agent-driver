"""Todo progress hints and periodic reminders for single-agent runtime."""

from __future__ import annotations

from agent_driver.contracts.context import PlanningState
from agent_driver.contracts.enums import ChatRole, PlanningTodoStatus
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult

TODO_REMINDER_TOOL_LOOPS = 4

SUBSTANTIVE_TODO_HINT_TOOLS = frozenset(
    {"web_search", "web_fetch", "read_file", "grep_search", "glob_search"}
)


def increment_tool_loops_since_todo_write(context: RunContext) -> None:
    """Count tool-loop iterations since the last successful todo_write."""
    loops = int(context.metadata.get("tool_loops_since_todo_write", 0))
    context.metadata["tool_loops_since_todo_write"] = loops + 1


def reset_todo_write_loop_counters(
    context: RunContext, *, in_progress_id: str | None
) -> None:
    """Reset loop and hint counters after a successful todo_write."""
    context.metadata["tool_loops_since_todo_write"] = 0
    context.metadata.pop("todo_hint_for_id", None)
    if in_progress_id:
        context.metadata["last_in_progress_id"] = in_progress_id


def planning_state_from_metadata(context: RunContext) -> PlanningState | None:
    payload = context.metadata.get("planning_state")
    if not isinstance(payload, dict):
        return None
    state = PlanningState.model_validate(payload)
    if not state.todos:
        return None
    return state


def active_in_progress_todo(state: PlanningState) -> tuple[str, str] | None:
    active = [
        (item.todo_id, item.content)
        for item in state.todos
        if item.status == PlanningTodoStatus.IN_PROGRESS
    ]
    if len(active) != 1:
        return None
    return active[0]


def format_todo_list_reminder(state: PlanningState) -> str:
    lines = ["Reminder: active session plan (update via todo_write merge=true):"]
    for item in state.todos:
        lines.append(f"[{item.status.value}] {item.content}")
    lines.append(
        "Mark each step completed immediately when done; keep exactly one "
        "in_progress. Do not repeat the full checklist in chat — the plan "
        "panel is the checklist."
    )
    return "\n".join(lines)


def maybe_append_todo_reminder_to_protocol(
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> tuple[ChatMessage, ...] | None:
    """Append a model-facing todo reminder when tool loops exceed the threshold."""
    if protocol_messages is None:
        return None
    threshold = int(
        context.metadata.get("todo_reminder_tool_loops", TODO_REMINDER_TOOL_LOOPS)
    )
    loops = int(context.metadata.get("tool_loops_since_todo_write", 0))
    if loops < threshold:
        return protocol_messages
    state = planning_state_from_metadata(context)
    if state is None:
        return protocol_messages
    return protocol_messages + (
        ChatMessage(
            role=ChatRole.USER,
            content=format_todo_list_reminder(state),
            metadata={"kind": "todo_reminder"},
        ),
    )


def append_todo_progress_hint_after_substantive_tool(
    context: RunContext,
    result: ToolExecutionResult,
    messages: list[ChatMessage],
) -> None:
    """Nudge the model to close the active step after substantive tool success."""
    if any(
        envelope.call.tool_name == "todo_write" for envelope in result.envelopes
    ):
        return
    state = planning_state_from_metadata(context)
    if state is None:
        return
    active = active_in_progress_todo(state)
    if active is None:
        return
    todo_id, content = active
    if context.metadata.get("todo_hint_for_id") == todo_id:
        return
    substantive_ok = False
    for envelope in result.envelopes:
        if envelope.error is not None:
            continue
        if envelope.call.tool_name in SUBSTANTIVE_TODO_HINT_TOOLS:
            substantive_ok = True
            break
    if not substantive_ok:
        return
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"If step '{todo_id}' ({content}) is finished, call todo_write "
                "with merge=true: mark it completed and set the next step "
                "in_progress. The plan checklist is in the UI — do not repeat "
                "the checklist in chat."
            ),
            metadata={"kind": "todo_progress_hint"},
        )
    )
    context.metadata["todo_hint_for_id"] = todo_id


__all__ = [
    "SUBSTANTIVE_TODO_HINT_TOOLS",
    "TODO_REMINDER_TOOL_LOOPS",
    "active_in_progress_todo",
    "append_todo_progress_hint_after_substantive_tool",
    "format_todo_list_reminder",
    "increment_tool_loops_since_todo_write",
    "maybe_append_todo_reminder_to_protocol",
    "planning_state_from_metadata",
    "reset_todo_write_loop_counters",
]
