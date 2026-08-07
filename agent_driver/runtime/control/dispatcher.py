"""Step-boundary dispatcher for steering control commands.

Epic 030: every declared :class:`ControlKind` now has a wired status — it either
applies, or is resolved to a WARNING and marked FAILED. A kind is NEVER silently
dropped (the old ``return False`` for 7 of 11 kinds left them QUEUED, re-draining
every step with no signal). The failure is reported honestly (A6):
``control_payload_invalid`` (wired kind, bad payload),
``control_kind_not_implemented`` (recognized kind with no consumer in this
context — the subagent controls on the single-agent path), or
``control_kind_unsupported`` (kind unknown to this build).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from agent_driver.contracts.control import (
    CommandQueueItem,
    ControlKind,
    ControlPriority,
    LiveMessagePhase,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolPolicyInput
from agent_driver.runtime.control.protocols import CommandQueueStore
from agent_driver.runtime.metadata_state import get_planning_runtime_state
from agent_driver.runtime.single_agent.types import RunContext

# A control event emitter: called with a raw-free payload for a WARNING event.
ControlEmit = Callable[[dict[str, Any]], None]
TransitionEmit = Callable[[CommandQueueItem], None]


class _Result(Enum):
    APPLIED = "applied"
    INVALID = "invalid"  # malformed payload — mark FAILED
    # Recognized kind with no consumer in THIS execution context (e.g. subagent
    # controls on the single-agent chat dispatcher) — honest signal + FAIL, kept
    # distinct from a genuinely-unknown kind so a host can tell "feature gap" from
    # "version mismatch / typo".
    NOT_IMPLEMENTED = "not_implemented"
    UNSUPPORTED = "unsupported"  # unknown kind — signal + FAIL


def drain_step_boundary_controls(
    *,
    context: RunContext,
    store: CommandQueueStore | None,
    abort_handle: Any = None,
    emit: ControlEmit | None = None,
    phase: LiveMessagePhase = LiveMessagePhase.TOOL_IN_FLIGHT,
    claimant_id: str = "runner",
    persist: Callable[[], None] | None = None,
    transition: TransitionEmit | None = None,
) -> list[CommandQueueItem]:
    """Apply pending current-turn controls at one safe run boundary.

    ``abort_handle`` bridges INTERRUPT into the run's cancellation seam so the
    control plane and the host ``/cancel`` path share one signal (epic 030 A).
    ``emit`` (when set) surfaces the unsupported / not-implemented /
    invalid-payload WARNINGs so the operator sees an unhandled command, not a
    silent no-op.
    """
    if store is None:
        return []
    applied: list[CommandQueueItem] = []
    claim = getattr(store, "claim_for_boundary", None)
    while True:
        if callable(claim):
            item = claim(
                run_id=context.run_id,
                claimant_id=claimant_id,
                applied_phase=phase,
            )
            if item is None:
                break
        else:
            item = next(
                (
                    pending
                    for pending in store.list_pending()
                    if pending.priority != ControlPriority.LATER
                    and not (
                        pending.kind == ControlKind.ENQUEUE_USER_MESSAGE
                        and pending.priority == ControlPriority.NEXT
                    )
                    and _matches_context(pending, context)
                ),
                None,
            )
            if item is None:
                break
        result = _apply_control_item(
            context, item, store=store, abort_handle=abort_handle, emit=emit
        )
        if result is _Result.APPLIED:
            if persist is not None:
                persist()
            try:
                marked = store.mark_applied(
                    item.queue_id,
                    claimant_id=claimant_id,
                    applied_phase=phase,
                )
            except TypeError:  # compatibility with pre-v1 custom stores
                marked = store.mark_applied(item.queue_id)
            applied_item = marked or item
            applied.append(applied_item)
            if transition is not None:
                transition(applied_item)
            if item.kind == ControlKind.INTERRUPT:
                stop_run = getattr(store, "stop_run", None)
                if callable(stop_run):
                    stopped = stop_run(context.run_id)
                    if transition is not None:
                        for stopped_item in stopped:
                            transition(stopped_item)
                break
        elif result is _Result.INVALID:
            failed = store.mark_failed(item.queue_id, error="invalid_control_payload")
            if transition is not None and failed is not None:
                transition(failed)
            _emit_control_warning(emit, item, signal_id="control_payload_invalid")
        elif result is _Result.NOT_IMPLEMENTED:
            failed = store.mark_failed(
                item.queue_id, error="control_kind_not_implemented"
            )
            if transition is not None and failed is not None:
                transition(failed)
            _emit_control_warning(emit, item, signal_id="control_kind_not_implemented")
        else:  # UNSUPPORTED
            failed = store.mark_failed(item.queue_id, error="control_kind_unsupported")
            if transition is not None and failed is not None:
                transition(failed)
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
    if kind == ControlKind.SET_MAX_THINKING_TOKENS:
        # A6: cap (or disable) the model's thinking/reasoning budget for subsequent
        # LLM calls. Mirrors SET_MODEL — the value rides tool_policy.metadata and is
        # consumed at request-build time into the provider-neutral reasoning envelope.
        tokens = item.payload.get("max_thinking_tokens", item.payload.get("tokens"))
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            return _Result.INVALID
        policy = context.run_input.tool_policy
        metadata = dict(policy.metadata)
        metadata["reasoning_max_tokens"] = tokens
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
        _append_user_message(context, message.strip(), queue_id=item.queue_id)
        return _Result.APPLIED
    if kind == ControlKind.STEER_USER_MESSAGE:
        message = item.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return _Result.INVALID
        _apply_soft_steer(context, message.strip(), queue_id=item.queue_id)
        return _Result.APPLIED
    if kind == ControlKind.PAUSE:
        # A3: request a boundary pause. The LLM step, right after this drain, parks the
        # run as PAUSED (resumable) instead of calling the provider. Non-destructive.
        context.metadata["steering_pause_requested"] = True
        return _Result.APPLIED
    if kind == ControlKind.REDIRECT_USER_MESSAGE:
        # A REDIRECT that reaches the STEP BOUNDARY (not mid-LLM-await) means there
        # was nothing in-flight to abort — degrade to enqueue (hermes: redirect
        # degrades to steer during the tool phase). The live mid-await abort path
        # is driven separately by the redirect_probe in the completion step.
        message = item.payload.get("message") or item.payload.get("text")
        if not isinstance(message, str) or not message.strip():
            return _Result.INVALID
        _append_user_message(context, message.strip(), queue_id=item.queue_id)
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
            cancel_next = getattr(store, "cancel_next", None)
            if callable(cancel_next):
                cancel_next(target.strip())
            else:
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
    if kind in (ControlKind.STOP_SUBAGENT, ControlKind.CONTINUE_SUBAGENT):
        # Recognized, but the single-agent chat dispatcher holds only the command
        # queue — it has no subagent store or child abort handle in scope (those live
        # in the tool stage / subagent executor, seam 2 in docs/live-message-controls).
        # Report a distinct not-implemented signal rather than a silent drop or a
        # conflation with an unknown kind.
        return _Result.NOT_IMPLEMENTED
    return _Result.UNSUPPORTED


def _append_user_message(
    context: RunContext, message: str, *, queue_id: str | None = None
) -> None:
    messages = list(context.run_input.messages)
    if queue_id is not None and any(
        item.metadata.get("live_message_queue_id") == queue_id for item in messages
    ):
        return
    if not messages and (context.run_input.input or "").strip():
        messages.append(
            ChatMessage(role=ChatRole.USER, content=context.run_input.input or "")
        )
    appended = ChatMessage(
        role=ChatRole.USER,
        content=message,
        metadata=(
            {"live_message_queue_id": queue_id} if queue_id is not None else {}
        ),
    )
    messages.append(appended)
    context.run_input = context.run_input.model_copy(
        update={"input": message, "messages": messages}
    )
    protocol = context.metadata.get("protocol_messages")
    if isinstance(protocol, list) and not any(
        isinstance(row, dict)
        and row.get("metadata", {}).get("live_message_queue_id") == queue_id
        for row in protocol
        if queue_id is not None
    ):
        protocol.append(appended.model_dump(mode="json"))
        context.metadata["protocol_messages"] = protocol


def _is_tool_role(role: Any) -> bool:
    return str(getattr(role, "value", role) or "").casefold() == "tool"


def _apply_soft_steer(
    context: RunContext, message: str, *, queue_id: str | None = None
) -> None:
    """Soft steer: fold user guidance into the CURRENT turn without a new user turn and
    without aborting — append it to the last tool-result message so it rides the next
    LLM call as guidance on the work in progress (alternation-safe, hermes model). No
    tool message to fold into (e.g. the model has not called a tool yet) degrades to a
    normal user-turn enqueue so the guidance is never dropped."""
    messages = list(context.run_input.messages)
    if queue_id is not None and any(
        item.metadata.get("live_message_queue_id") == queue_id for item in messages
    ):
        return  # idempotent: this steer already landed
    last_tool = next(
        (i for i in range(len(messages) - 1, -1, -1) if _is_tool_role(messages[i].role)),
        None,
    )
    if last_tool is None:
        _append_user_message(context, message, queue_id=queue_id)
        return
    note = f"\n\n[User steering: {message}]"
    tool_msg = messages[last_tool]
    metadata = dict(tool_msg.metadata or {})
    if queue_id is not None:
        metadata["live_message_queue_id"] = queue_id
    folded = tool_msg.model_copy(
        update={"content": str(tool_msg.content or "") + note, "metadata": metadata}
    )
    messages[last_tool] = folded
    context.run_input = context.run_input.model_copy(update={"messages": messages})
    # Mirror the fold into the durable protocol log so resume/compaction see it too.
    protocol = context.metadata.get("protocol_messages")
    if isinstance(protocol, list):
        if queue_id is not None and any(
            isinstance(row, dict)
            and row.get("metadata", {}).get("live_message_queue_id") == queue_id
            for row in protocol
        ):
            return
        for row in reversed(protocol):
            if isinstance(row, dict) and _is_tool_role(row.get("role")):
                row["content"] = str(row.get("content") or "") + note
                row_meta = dict(row.get("metadata") or {})
                if queue_id is not None:
                    row_meta["live_message_queue_id"] = queue_id
                row["metadata"] = row_meta
                break
        context.metadata["protocol_messages"] = protocol


__all__ = ["drain_step_boundary_controls"]
