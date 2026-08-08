"""Todo progress hints and periodic reminders for single-agent runtime."""

from __future__ import annotations

from agent_driver.contracts.context import PlanningState
from agent_driver.contracts.enums import ChatRole, PlanningTodoStatus
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import scaffolding_metadata
from agent_driver.runtime.metadata_state import get_planning_runtime_state
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult

TODO_REMINDER_TOOL_LOOPS = 2

# P4: once the in_progress step has survived this many tool loops with no todo_write
# update, escalate the periodic reminder — the step is stuck and the model needs to
# finish, split, or cancel it rather than keep spinning. Higher than the reminder
# threshold so the escalation only kicks in after the normal nudge has been ignored.
TODO_STALE_TOOL_LOOPS = 5

SUBSTANTIVE_TODO_HINT_TOOLS = frozenset(
    {"web_search", "web_fetch", "read_file", "grep_search", "glob_search"}
)


def increment_tool_loops_since_todo_write(context: RunContext) -> None:
    """Count tool-loop iterations since the last successful todo_write."""
    get_planning_runtime_state(context).increment_tool_loops_since_todo_write()


def reset_todo_write_loop_counters(
    context: RunContext, *, in_progress_id: str | None
) -> None:
    """Reset loop and hint counters after a successful todo_write."""
    get_planning_runtime_state(context).reset_todo_write_loop_counters(
        in_progress_id=in_progress_id
    )


def planning_state_from_metadata(context: RunContext) -> PlanningState | None:
    payload = get_planning_runtime_state(context).planning_state()
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


def unfinished_todos(state: PlanningState) -> list[tuple[str, str, PlanningTodoStatus]]:
    """Return todos that still require work before a planned run can finish."""
    return [
        (item.todo_id, item.content, item.status)
        for item in state.todos
        if item.status
        in {
            PlanningTodoStatus.PENDING,
            PlanningTodoStatus.IN_PROGRESS,
        }
    ]


def has_unfinished_todos(context: RunContext) -> bool:
    """Return whether persisted planning state still has open work."""
    state = planning_state_from_metadata(context)
    if state is None:
        return False
    return bool(unfinished_todos(state))


def format_open_todos_finalize_reminder(state: PlanningState) -> str:
    """Strong reminder injected when a finalize attempt was blocked by open todos (P1)."""
    lines = [
        "You attempted to give a final answer, but the session plan still has open work:"
    ]
    for _todo_id, content, status in unfinished_todos(state):
        lines.append(f"[{status.value}] {content}")
    lines.append(
        "Finish these steps now. If a step is no longer needed, cancel it with "
        "todo_write(merge=true) setting its status to cancelled. Do NOT give the final "
        "answer until every todo is completed or cancelled."
    )
    return "\n".join(lines)


def format_todo_list_reminder(state: PlanningState) -> str:
    """Render the periodic plan reminder — ACTIVE items only (P3).

    Only pending/in_progress steps are listed. Re-listing completed/cancelled steps is a
    known way to make a model re-do finished work after a context compaction (the todo
    list persists in metadata while the message history that recorded the work is
    summarized away), so those are collapsed into a "N already done — do NOT redo" note
    instead of a re-executable line (hermes' compaction rule).
    """
    active = unfinished_todos(state)
    done_count = len(state.todos) - len(active)
    lines = ["Reminder: active session plan (update via todo_write merge=true)."]
    if done_count:
        lines.append(
            f"{done_count} of {len(state.todos)} steps are already completed/cancelled "
            "— do NOT redo them."
        )
    if active:
        lines.append("Remaining steps:")
        for _todo_id, content, status in active:
            lines.append(f"[{status.value}] {content}")
        lines.append(
            "Mark each completed immediately when done; keep exactly one in_progress. "
            "Do not repeat the full checklist in chat — the plan panel is the checklist."
        )
    else:
        lines.append("All planned steps are done — produce the final answer now.")
    return "\n".join(lines)


# P5: stems that mark a plan step as a verification/review step (EN + RU). Substring
# match against the lowered content, so "verify", "verification", "double-check",
# "проверка", "перепроверь" all count.
_VERIFICATION_STEM = (
    "verif",
    "verify",
    "check",
    "test",
    "review",
    "validat",
    "audit",
    "провер",
    "перепровер",
    "тест",
    "валидац",
    "сверк",
    "сверь",
    "ревью",
    "аудит",
)


def plan_all_done_without_verification(
    state: PlanningState, *, min_steps: int = 3
) -> bool:
    """Return whether a substantial plan finished with no verification step (P5).

    True only when the plan has ``>= min_steps`` todos, every one is
    completed/cancelled, and none reads like a verification/review step — the case
    where the model is about to declare a multi-step task done without checking its work.
    """
    if len(state.todos) < min_steps:
        return False
    if unfinished_todos(state):
        return False
    for item in state.todos:
        lowered = item.content.lower()
        if any(stem in lowered for stem in _VERIFICATION_STEM):
            return False
    return True


def format_verify_before_final_reminder(state: PlanningState) -> str:
    """Nudge to verify a completed multi-step plan before the final answer (P5)."""
    return (
        f"You completed a {len(state.todos)}-step plan, but none of the steps was a "
        "verification step. Before the final answer, verify your work — re-check the key "
        "results/outputs you produced (recompute a figure, re-open a source, re-read the "
        "artifact). Do not declare the task done by only listing caveats. Once you have "
        "verified, give the final answer."
    )


def format_stale_todo_escalation(state: PlanningState, loops: int) -> str:
    """Return an escalation for a step stuck in_progress too long, or "" (P4).

    Only escalates when exactly one step is in_progress (the invariant), naming it so the
    model resolves that specific step instead of spinning.
    """
    active = active_in_progress_todo(state)
    if active is None:
        return ""
    _todo_id, content = active
    return (
        f"Step '{content}' has been in progress for {loops} tool steps without "
        "completing. Finish it now; if it is too large, split it into smaller "
        "todo_write steps; if you are blocked, cancel it (todo_write merge=true, status "
        "cancelled) and move on. Do not keep spinning on the same step."
    )


def maybe_append_todo_reminder_to_protocol(
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> tuple[ChatMessage, ...] | None:
    """Append a model-facing todo reminder when tool loops exceed the threshold."""
    if protocol_messages is None:
        return None
    planning_state = get_planning_runtime_state(context)
    # P1: a finalize attempt was just blocked because todos remain open — inject the
    # concrete "finish or cancel" instruction with the open items, regardless of the
    # periodic loop threshold. The marker is one-shot (cleared here) so it rides exactly
    # the re-prompt turn it was set for.
    if context.metadata.pop("open_todos_finalize_blocked", None):
        state = planning_state_from_metadata(context)
        if state is not None:
            return protocol_messages + (
                ChatMessage(
                    role=ChatRole.USER,
                    content=format_open_todos_finalize_reminder(state),
                    metadata=scaffolding_metadata(
                        "open_todos_finalize_reminder",
                        base={"kind": "open_todos_finalize_reminder"},
                    ),
                ),
            )
    # P5: a finalize attempt was blocked because a completed multi-step plan had no
    # verification step — inject the "verify your work first" nudge (one-shot marker).
    if context.metadata.pop("verify_before_final_blocked", None):
        state = planning_state_from_metadata(context)
        if state is not None:
            return protocol_messages + (
                ChatMessage(
                    role=ChatRole.USER,
                    content=format_verify_before_final_reminder(state),
                    metadata=scaffolding_metadata(
                        "verify_before_final_reminder",
                        base={"kind": "verify_before_final_reminder"},
                    ),
                ),
            )
    threshold = planning_state.todo_reminder_tool_loops(TODO_REMINDER_TOOL_LOOPS)
    loops = planning_state.tool_loops_since_todo_write()
    if loops < threshold:
        return protocol_messages
    state = planning_state_from_metadata(context)
    if state is None:
        return protocol_messages
    content = format_todo_list_reminder(state)
    # P4: escalate when the current step has been stuck in_progress across many loops.
    if loops >= TODO_STALE_TOOL_LOOPS:
        escalation = format_stale_todo_escalation(state, loops)
        if escalation:
            content = content + "\n" + escalation
    return protocol_messages + (
        ChatMessage(
            role=ChatRole.USER,
            content=content,
            metadata=scaffolding_metadata("todo_reminder", base={"kind": "todo_reminder"}),
        ),
    )


def append_todo_progress_hint_after_substantive_tool(
    context: RunContext,
    result: ToolExecutionResult,
    messages: list[ChatMessage],
) -> None:
    """Nudge the model to close the active step after substantive tool success."""
    if any(envelope.call.tool_name == "todo_write" for envelope in result.envelopes):
        return
    state = planning_state_from_metadata(context)
    if state is None:
        return
    active = active_in_progress_todo(state)
    if active is None:
        return
    todo_id, content = active
    planning_state = get_planning_runtime_state(context)
    hint_count = planning_state.todo_hint_count(todo_id)
    if hint_count >= 2:
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
            metadata=scaffolding_metadata(
                "todo_progress_hint", base={"kind": "todo_progress_hint"}
            ),
        )
    )
    planning_state.increment_todo_hint_count(todo_id)


__all__ = [
    "SUBSTANTIVE_TODO_HINT_TOOLS",
    "TODO_REMINDER_TOOL_LOOPS",
    "active_in_progress_todo",
    "TODO_STALE_TOOL_LOOPS",
    "append_todo_progress_hint_after_substantive_tool",
    "format_open_todos_finalize_reminder",
    "format_stale_todo_escalation",
    "format_todo_list_reminder",
    "format_verify_before_final_reminder",
    "plan_all_done_without_verification",
    "increment_tool_loops_since_todo_write",
    "maybe_append_todo_reminder_to_protocol",
    "planning_state_from_metadata",
    "reset_todo_write_loop_counters",
    "has_unfinished_todos",
    "unfinished_todos",
]
