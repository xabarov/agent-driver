"""Host-friendly normalizers between HTTP-style payloads and ResumeCommand.

This module bridges the gap between the loose JSON bodies that host
applications typically receive on HITL endpoints (e.g.
``POST /api/conversations/{id}/approve``) and the typed
``ResumeCommand`` that ``Agent.resume(...)`` expects.

Two helpers are provided, both intentionally domain-neutral:

- :func:`resume_command_from_payload` — normalize a JSON body / form dict
  into a ``ResumeCommand``. Accepts three input families: explicit
  ``action`` strings, legacy ``choice`` integers (``1`` approve / ``2`` edit /
  ``3`` cancel), and opaque ``resume`` / ``answer`` / ``value`` fields that
  many LangGraph-style applications use to pass an arbitrary user response
  back to the runtime.
- :func:`interrupt_to_stream_event` — turn an :class:`InterruptRequest`
  into a transport-neutral ``dict`` that hosts can wrap in their own SSE /
  WebSocket envelope (e.g. ``{"type": "plan.proposed", "payload": ...}``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import (
    ApprovalPayload,
    InterruptRequest,
    ResumeCommand,
)

ValueToAction = Callable[[Any], "ResumeAction | None"]

_ACTION_ALIASES: dict[str, ResumeAction] = {
    "approve": ResumeAction.APPROVE,
    "approved": ResumeAction.APPROVE,
    "accept": ResumeAction.APPROVE,
    "yes": ResumeAction.APPROVE,
    "reject": ResumeAction.REJECT,
    "rejected": ResumeAction.REJECT,
    "deny": ResumeAction.REJECT,
    "no": ResumeAction.REJECT,
    "edit": ResumeAction.EDIT,
    "modify": ResumeAction.EDIT,
    "modified": ResumeAction.EDIT,
    "cancel": ResumeAction.CANCEL,
    "cancelled": ResumeAction.CANCEL,
    "abort": ResumeAction.CANCEL,
    "clarify": ResumeAction.CLARIFY,
    "clarification": ResumeAction.CLARIFY,
    "patch_state": ResumeAction.PATCH_STATE,
    "patch-state": ResumeAction.PATCH_STATE,
    "state_patch": ResumeAction.PATCH_STATE,
}

_LEGACY_CHOICE_MAP: dict[int, ResumeAction] = {
    1: ResumeAction.APPROVE,
    2: ResumeAction.EDIT,
    3: ResumeAction.CANCEL,
}


def _coerce_action_alias(value: Any) -> ResumeAction | None:
    if isinstance(value, ResumeAction):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ACTION_ALIASES:
            return _ACTION_ALIASES[normalized]
    return None


def _default_value_to_action(  # pylint: disable=too-many-return-statements
    value: Any,
) -> ResumeAction | None:
    """Map an opaque ``resume`` / ``answer`` / ``value`` field to a ResumeAction.

    Defaults used when the host does not supply its own mapper:

    - boolean ``True`` / ``False`` and integer ``0`` / ``1`` map to
      APPROVE / REJECT respectively (treat ``1``/``True`` as affirmative);
    - string aliases (``yes``/``approve``/``no``/``reject``/``cancel``/...)
      via the standard alias table;
    - dict payloads → EDIT (the caller is expected to extract the edited
      arguments separately into ``edited_tool_args``);
    - non-empty unrecognized strings → CLARIFY (free-text response);
    - empty / ``None`` → ``None`` (caller must raise).
    """
    if isinstance(value, ResumeAction):
        return value
    if value is None:
        return None
    if isinstance(value, bool):
        return ResumeAction.APPROVE if value else ResumeAction.REJECT
    if isinstance(value, int):
        if value == 1:
            return ResumeAction.APPROVE
        if value == 0:
            return ResumeAction.REJECT
        if value in _LEGACY_CHOICE_MAP:
            return _LEGACY_CHOICE_MAP[value]
        return None
    if isinstance(value, str):
        alias = _coerce_action_alias(value)
        if alias is not None:
            return alias
        if value.strip():
            return ResumeAction.CLARIFY
        return None
    if isinstance(value, dict):
        return ResumeAction.EDIT
    if isinstance(value, list):
        return ResumeAction.CLARIFY if value else None
    return None


def _opaque_value_message(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return text or None
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return None
    return None


def _resolve_interrupt_id(
    body: Mapping[str, Any], default_interrupt_id: str | None
) -> str:
    raw = body.get("interrupt_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if not default_interrupt_id:
        raise ValueError(
            "interrupt_id missing from payload and no default_interrupt_id supplied"
        )
    return default_interrupt_id


def _resolve_action_and_inferred(
    body: Mapping[str, Any], value_to_action: ValueToAction | None
) -> tuple[ResumeAction, str | None, dict[str, Any] | None]:
    if "action" in body:
        action = _coerce_action_alias(body["action"])
        if action is None:
            raise ValueError(f"unknown action: {body['action']!r}")
        return action, None, None
    if "choice" in body:
        choice = body["choice"]
        if not isinstance(choice, int) or choice not in _LEGACY_CHOICE_MAP:
            raise ValueError(
                f"unknown choice: {choice!r}; expected 1 (approve), 2 (edit), 3 (cancel)"
            )
        return _LEGACY_CHOICE_MAP[choice], None, None
    opaque_value = body.get("resume", body.get("answer", body.get("value")))
    resolver = value_to_action or _default_value_to_action
    action = resolver(opaque_value)
    if action is None:
        raise ValueError(
            "cannot infer ResumeAction from payload: provide 'action', 'choice', "
            "or 'resume'/'answer'/'value' with a recognized shape"
        )
    inferred_message: str | None = None
    inferred_edited_tool_args: dict[str, Any] | None = None
    if action is ResumeAction.CLARIFY:
        inferred_message = _opaque_value_message(opaque_value)
    elif action is ResumeAction.EDIT and isinstance(opaque_value, dict):
        inferred_edited_tool_args = dict(opaque_value)
    return action, inferred_message, inferred_edited_tool_args


def _validated_optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string when present")
    return value


def _validated_optional_mapping(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping when present")
    return dict(value)


def resume_command_from_payload(
    payload: Mapping[str, Any],
    *,
    default_interrupt_id: str | None = None,
    value_to_action: ValueToAction | None = None,
) -> ResumeCommand:
    """Normalize a host HTTP body / form dict into a typed ``ResumeCommand``.

    Recognized keys (all optional except where noted):

    - ``interrupt_id`` — explicit interrupt id; overrides ``default_interrupt_id``;
    - ``action`` — explicit ``ResumeAction`` value or string alias
      (``approve|reject|edit|cancel|clarify|patch_state``);
    - ``choice`` — legacy integer (``1`` approve / ``2`` edit / ``3`` cancel);
    - ``resume`` / ``answer`` / ``value`` — opaque user response, mapped via
      ``value_to_action`` or a default rule (see
      :func:`_default_value_to_action`);
    - ``message`` — clarification or reject explanation;
    - ``edited_tool_args`` — for ``EDIT``; if absent and ``resume`` is a dict,
      that dict is used as ``edited_tool_args``;
    - ``state_patch`` — for ``PATCH_STATE``;
    - ``approved_by`` — actor name (operator id, username, ...);
    - ``metadata`` — pass-through metadata dict.

    Priority order when multiple shape hints are present:

    1. explicit ``action`` field;
    2. legacy ``choice`` integer;
    3. opaque ``resume`` / ``answer`` / ``value`` field via ``value_to_action``;
    4. otherwise ``ValueError`` is raised so the host can return ``400``.

    The function does not perform domain-specific validation (e.g. that
    ``approved_by`` is set for APPROVE) — that policy belongs to the host
    application or to the agent runtime that consumes the ``ResumeCommand``.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a Mapping")
    body = dict(payload)

    interrupt_id = _resolve_interrupt_id(body, default_interrupt_id)
    action, inferred_message, inferred_edited_tool_args = _resolve_action_and_inferred(
        body, value_to_action
    )

    message = _validated_optional_string(body.get("message"), "message")
    if message is None:
        message = inferred_message

    edited_tool_args = _validated_optional_mapping(
        body.get("edited_tool_args"), "edited_tool_args"
    )
    if edited_tool_args is None:
        edited_tool_args = inferred_edited_tool_args

    state_patch = _validated_optional_mapping(body.get("state_patch"), "state_patch")
    approved_by = _validated_optional_string(body.get("approved_by"), "approved_by")

    metadata = _validated_optional_mapping(body.get("metadata"), "metadata") or {}

    return ResumeCommand(
        interrupt_id=interrupt_id,
        action=action,
        message=message if message else None,
        edited_tool_args=edited_tool_args,
        state_patch=state_patch,
        approved_by=approved_by,
        metadata=metadata,
    )


def interrupt_to_stream_event(
    interrupt: InterruptRequest,
    *,
    args_preview_chars: int = 280,
    event_type: str = "interrupt_requested",
) -> dict[str, Any]:
    """Return a transport-neutral dict representing one interrupt event.

    Hosts wrap this dict in their own SSE / WebSocket envelope. Common
    patterns:

    - LangGraph-style ``plan.proposed`` event::

          yield sse_event({"type": "plan.proposed", "payload": projection})

    - generic interrupt event::

          yield sse_event({"type": "interrupt", "data": projection})

    The dict contains:

    - ``type`` — the ``event_type`` argument (default ``"interrupt_requested"``);
    - ``interrupt_id``, ``run_id``, ``attempt_id``, ``checkpoint_id``;
    - ``reason``, ``title``, ``description``;
    - ``risk`` — ``"low"`` / ``"medium"`` / ``"high"`` or ``None``;
    - ``allowed_actions`` — list of action strings (``"approve"``, ...);
    - ``editable_fields`` — list of field names the host may edit;
    - ``expires_at`` — ISO timestamp or ``None``;
    - ``proposed_action`` — raw runtime payload (tool args, plan ids, etc.);
    - ``approval_payload`` — deterministic UI-facing approval card derived
      from the interrupt (see :class:`ApprovalPayload`);
    - ``metadata`` — pass-through metadata.

    ``args_preview_chars`` is forwarded to
    :meth:`ApprovalPayload.from_interrupt` for ``args_preview`` truncation.
    """
    approval_payload = ApprovalPayload.from_interrupt(
        interrupt, args_preview_chars=args_preview_chars
    )
    return {
        "type": event_type,
        "interrupt_id": interrupt.interrupt_id,
        "run_id": interrupt.run_id,
        "attempt_id": interrupt.attempt_id,
        "checkpoint_id": interrupt.checkpoint_id,
        "reason": interrupt.reason.value,
        "title": interrupt.title,
        "description": interrupt.description,
        "risk": interrupt.risk.value if interrupt.risk is not None else None,
        "allowed_actions": [action.value for action in interrupt.allowed_actions],
        "editable_fields": list(interrupt.editable_fields),
        "expires_at": interrupt.expires_at,
        "proposed_action": dict(interrupt.proposed_action),
        "approval_payload": approval_payload.model_dump(mode="json"),
        "metadata": dict(interrupt.metadata),
    }


__all__ = [
    "ValueToAction",
    "interrupt_to_stream_event",
    "resume_command_from_payload",
]
