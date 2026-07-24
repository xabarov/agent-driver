"""Step-boundary dispatcher for steering control commands.

Epic 030: every declared :class:`ControlKind` now has a wired status — it either
applies, or (for a kind not supported on this run path) emits a
``control_kind_unsupported`` WARNING and is marked FAILED. A kind is NEVER
silently dropped (the old ``return False`` for 7 of 11 kinds left them QUEUED,
re-draining every step with no signal).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from agent_driver.contracts.control import (
    CommandQueueItem,
    ControlKind,
    ControlPriority,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolPolicyInput
from agent_driver.runtime.control.protocols import CommandQueueStore
from agent_driver.runtime.metadata_state import get_planning_runtime_state
from agent_driver.runtime.single_agent.types import RunContext

# A control event emitter: called with a raw-free payload for a WARNING event.
ControlEmit = Callable[[dict[str, Any]], None]


class _Result(Enum):
    APPLIED = "applied"
    INVALID = "invalid"  # malformed payload — mark FAILED
    UNSUPPORTED = "unsupported"  # kind not wired on this path — signal + FAIL


def drain_step_boundary_controls(
    *,
    context: RunContext,
    store: CommandQueueStore | None,
    abort_handle: Any = None,
    emit: ControlEmit | None = None,
) -> list[CommandQueueItem]:
    """Apply pending now/next controls for this run boundary.

    ``abort_handle`` bridges INTERRUPT into the run's cancellation seam so the
    control plane and the host ``/cancel`` path share one signal (epic 030 A).
    ``emit`` (when set) surfaces ``control_kind_unsupported`` / invalid-payload
    WARNINGs so the operator sees an unhandled command, not a silent no-op.
    """
    if store is None:
        return []
    applied: list[CommandQueueItem] = []
    for item in store.list_pending():
        if item.priority == ControlPriority.LATER:
            continue
        if not _matches_context(item, context):
            continue
        result = _apply_control_item(
            context, item, store=store, abort_handle=abort_handle, emit=emit
        )
        if result is _Result.APPLIED:
            marked = store.mark_applied(item.queue_id)
            applied.append(marked or item)
        elif result is _Result.INVALID:
            store.mark_failed(item.queue_id, error="invalid_control_payload")
            _emit_control_warning(emit, item, signal_id="control_payload_invalid")
        else:  # UNSUPPORTED
            store.mark_failed(item.queue_id, error="control_kind_unsupported")
            _emit_control_warning(emit, item, signal_id="control_kind_unsupported")
    if applied:
        existing = context.metadata.get("applied_controls")
        if not isinstance(existing, list):
            existing = []
        existing.extend(item.model_dump(mode="json") for item in applied)
        context.metadata["applied_controls"] = existing
    return applied


def _emit_control_warning(
    emit: ControlEmit | None, item: CommandQueueItem, *, signal_id: str
) -> None:
    if emit is None:
        return
    emit(
        {
            "signal_id": signal_id,
            "severity": "warning",
            "control_kind": str(item.kind),
            "queue_id": item.queue_id,
            "raw_free": True,
        }
    )


def _matches_context(item: CommandQueueItem, context: RunContext) -> bool:
    if item.run_id is not None and item.run_id == context.run_id:
        return True
    thread_id = context.run_input.thread_id
    if item.thread_id is not None and item.thread_id == thread_id:
        return True
    if item.agent_id is not None and item.agent_id == context.run_input.agent_id:
        return True
    return False


def _apply_control_item(
    context: RunContext,
    item: CommandQueueItem,
    *,
    store: CommandQueueStore,
    abort_handle: Any,
    emit: ControlEmit | None,
) -> _Result:
    kind = item.kind
    if kind == ControlKind.SET_MODEL:
        model = item.payload.get("model")
        if not isinstance(model, str) or not model.strip():
            return _Result.INVALID
        policy = context.run_input.tool_policy
        metadata = dict(policy.metadata)
        metadata["forced_model"] = model.strip()
        context.run_input = context.run_input.model_copy(
            update={"tool_policy": policy.model_copy(update={"metadata": metadata})}
        )
        return _Result.APPLIED
    if kind == ControlKind.SET_PERMISSION_MODE:
        mode = item.payload.get("mode")
        if not isinstance(mode, str) or not mode.strip():
            return _Result.INVALID
        app_metadata = dict(context.run_input.app_metadata)
        app_metadata["permission_mode"] = mode.strip()
        context.run_input = context.run_input.model_copy(
            update={"app_metadata": app_metadata}
        )
        return _Result.APPLIED
    if kind == ControlKind.SET_TOOL_POLICY:
        payload = item.payload.get("tool_policy")
        if not isinstance(payload, dict):
            return _Result.INVALID
        context.run_input = context.run_input.model_copy(
            update={"tool_policy": ToolPolicyInput.model_validate(payload)}
        )
        return _Result.APPLIED
    if kind == ControlKind.ENQUEUE_USER_MESSAGE:
        message = item.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return _Result.INVALID
        _append_user_message(context, message.strip())
        return _Result.APPLIED
    if kind == ControlKind.REDIRECT_USER_MESSAGE:
        # A REDIRECT that reaches the STEP BOUNDARY (not mid-LLM-await) means there
        # was nothing in-flight to abort — degrade to enqueue (hermes: redirect
        # degrades to steer during the tool phase). The live mid-await abort path
        # is driven separately by the redirect_probe in the completion step.
        message = item.payload.get("message") or item.payload.get("text")
        if not isinstance(message, str) or not message.strip():
            return _Result.INVALID
        _append_user_message(context, message.strip())
        return _Result.APPLIED
    if kind == ControlKind.INTERRUPT:
        if abort_handle is not None and hasattr(abort_handle, "abort"):
            reason = item.payload.get("reason") or "steering_interrupt"
            try:
                abort_handle.abort(str(reason))
                return _Result.APPLIED
            except Exception:  # noqa: BLE001
                return _Result.INVALID
        return _Result.UNSUPPORTED
    if kind == ControlKind.CANCEL_QUEUED_MESSAGE:
        target = item.payload.get("queue_id")
        if not isinstance(target, str) or not target.strip():
            return _Result.INVALID
        try:
            store.cancel(target.strip())
        except Exception:  # noqa: BLE001
            return _Result.INVALID
        return _Result.APPLIED
    if kind == ControlKind.GET_CONTEXT_USAGE:
        # Read-only introspection: surface current token pressure into the
        # journal (raw-free counts). The consumer reads it off the event stream.
        if emit is not None:
            pressure = context.metadata.get("token_pressure")
            emit(
                {
                    "signal_id": "context_usage_report",
                    "severity": "info",
                    "step_count": context.step_count,
                    "token_pressure": pressure if isinstance(pressure, dict) else {},
                    "raw_free": True,
                }
            )
        return _Result.APPLIED
    if kind == ControlKind.PATCH_PLANNING_STATE:
        patch = item.payload.get("planning_state") or item.payload.get("patch")
        if not isinstance(patch, dict):
            return _Result.INVALID
        try:
            planning = get_planning_runtime_state(context)
            merged = {**(planning.planning_state() or {}), **patch}
            planning.set_planning_state(merged)
            return _Result.APPLIED
        except Exception:  # noqa: BLE001 - no mergeable planning state on this path
            return _Result.UNSUPPORTED
    # SET_MAX_THINKING_TOKENS, STOP_SUBAGENT, CONTINUE_SUBAGENT: not wired on the
    # single-agent chat path — honest signal instead of a silent drop.
    return _Result.UNSUPPORTED


def _append_user_message(context: RunContext, message: str) -> None:
    messages = list(context.run_input.messages)
    if not messages and (context.run_input.input or "").strip():
        messages.append(
            ChatMessage(role=ChatRole.USER, content=context.run_input.input or "")
        )
    messages.append(ChatMessage(role=ChatRole.USER, content=message))
    context.run_input = context.run_input.model_copy(
        update={"input": message, "messages": messages}
    )


__all__ = ["drain_step_boundary_controls"]
