"""Step-boundary dispatcher for steering control commands."""

from __future__ import annotations

from agent_driver.contracts.control import (
    CommandQueueItem,
    ControlKind,
    ControlPriority,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.tools import ToolPolicyInput
from agent_driver.runtime.control.protocols import CommandQueueStore
from agent_driver.runtime.single_agent.types import RunContext


def drain_step_boundary_controls(
    *,
    context: RunContext,
    store: CommandQueueStore | None,
) -> list[CommandQueueItem]:
    """Apply pending now/next controls for this run boundary."""
    if store is None:
        return []
    applied: list[CommandQueueItem] = []
    for item in store.list_pending():
        if item.priority == ControlPriority.LATER:
            continue
        if not _matches_context(item, context):
            continue
        if _apply_control_item(context, item):
            marked = store.mark_applied(item.queue_id)
            applied.append(marked or item)
    if applied:
        existing = context.metadata.get("applied_controls")
        if not isinstance(existing, list):
            existing = []
        existing.extend(item.model_dump(mode="json") for item in applied)
        context.metadata["applied_controls"] = existing
    return applied


def _matches_context(item: CommandQueueItem, context: RunContext) -> bool:
    if item.run_id is not None and item.run_id == context.run_id:
        return True
    thread_id = context.run_input.thread_id
    if item.thread_id is not None and item.thread_id == thread_id:
        return True
    if item.agent_id is not None and item.agent_id == context.run_input.agent_id:
        return True
    return False


def _apply_control_item(context: RunContext, item: CommandQueueItem) -> bool:
    if item.kind == ControlKind.SET_MODEL:
        model = item.payload.get("model")
        if not isinstance(model, str) or not model.strip():
            return False
        policy = context.run_input.tool_policy
        metadata = dict(policy.metadata)
        metadata["forced_model"] = model.strip()
        context.run_input = context.run_input.model_copy(
            update={
                "tool_policy": policy.model_copy(update={"metadata": metadata}),
            }
        )
        return True
    if item.kind == ControlKind.SET_PERMISSION_MODE:
        mode = item.payload.get("mode")
        if not isinstance(mode, str) or not mode.strip():
            return False
        app_metadata = dict(context.run_input.app_metadata)
        app_metadata["permission_mode"] = mode.strip()
        context.run_input = context.run_input.model_copy(
            update={"app_metadata": app_metadata}
        )
        return True
    if item.kind == ControlKind.SET_TOOL_POLICY:
        payload = item.payload.get("tool_policy")
        if not isinstance(payload, dict):
            return False
        context.run_input = context.run_input.model_copy(
            update={"tool_policy": ToolPolicyInput.model_validate(payload)}
        )
        return True
    if item.kind == ControlKind.ENQUEUE_USER_MESSAGE:
        message = item.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return False
        _append_user_message(context, message.strip())
        return True
    return False


def _append_user_message(context: RunContext, message: str) -> None:
    messages = list(context.run_input.messages)
    if not messages and (context.run_input.input or "").strip():
        messages.append(
            ChatMessage(role=ChatRole.USER, content=context.run_input.input or "")
        )
    messages.append(ChatMessage(role=ChatRole.USER, content=message))
    context.run_input = context.run_input.model_copy(
        update={
            "input": message,
            "messages": messages,
        }
    )


__all__ = ["drain_step_boundary_controls"]
