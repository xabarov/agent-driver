"""Warning-event projection helpers for application transport adapters.

This module gives host applications a stable, domain-neutral projection of the
runtime's ``RuntimeEventType.WARNING`` events. A host application typically
emits these events to its frontend via SSE/WebSocket; the application maps
``signal_id`` values to its own user-facing vocabulary (warning ids, message
copy, suggestion slugs, severity colour, etc.).

Stable signal ids
-----------------

For ``kind="token_pressure"``:

- ``context_above_soft_threshold`` — context usage entered the warning band;
  compaction is not yet recommended but the host may surface a hint.
- ``context_compact_recommended`` — context usage crossed the compact
  threshold; the runtime will attempt compaction on the next eligible turn.
- ``context_blocking_threshold`` — context usage approached the blocking
  threshold; the runtime may refuse further LLM calls until compaction runs.

For ``kind="tool_choice_antipattern"``:

- ``signal_id`` comes from the ``AntipatternMatch.pattern_id`` registered
  by the host. The shipped reference rule emits
  ``generic_after_specialized_search`` when a generic-shell tool is chosen
  immediately after a specialized search call.

Each signal carries a precomputed ``severity`` (``info|warning|critical``) and
the raw thresholds (``warning_threshold``, ``compact_threshold``,
``blocking_threshold``, ``context_window_estimate``, ``output_token_reserve``)
plus the derived ``usage_ratio``. The host can build its own message copy from
these numbers without re-deriving any thresholds.

Example
-------

::

    from agent_driver.adapters.warnings import project_warning_event

    async for stream_event in agent.stream(run_input):
        projection = project_warning_event(stream_event)
        if projection is None:
            continue
        # Application-side mapping signal_id -> host vocabulary
        if projection["signal_id"] == "context_above_soft_threshold":
            yield {
                "type": "context_warning",
                "warning_id": "context_above_80pct",
                "level": projection["severity"],
                "message": "...",
                "suggestion": "near_hard_threshold",
                "detail": {
                    "usage_ratio": projection["usage_ratio"],
                    ...
                },
            }
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.stream import RunStreamEvent

_TOKEN_PRESSURE_FIELDS: tuple[str, ...] = (
    "signal_id",
    "severity",
    "state",
    "used_tokens_estimate",
    "remaining_tokens_estimate",
    "context_window_estimate",
    "output_token_reserve",
    "warning_threshold",
    "compact_threshold",
    "blocking_threshold",
    "usage_ratio",
)

_TOOL_CHOICE_ANTIPATTERN_FIELDS: tuple[str, ...] = (
    "signal_id",
    "severity",
    "description",
    "matched_recent_tool",
    "matched_current_tool",
    "rule_metadata",
)

_KNOWN_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "token_pressure": _TOKEN_PRESSURE_FIELDS,
    "tool_choice_antipattern": _TOOL_CHOICE_ANTIPATTERN_FIELDS,
}


def project_warning_event(event: RunStreamEvent) -> dict[str, Any] | None:
    """Return a stable warning projection or ``None`` for non-warning events.

    The returned dict always contains:

    - ``kind`` — string tag identifying the warning family
      (currently only ``"token_pressure"``);
    - ``signal_id`` — stable identifier from the per-kind enumeration above;
    - ``severity`` — ``info``, ``warning``, or ``critical``;
    - ``data`` — raw per-kind fields, suitable for ``detail`` in host SSE
      payloads.

    Returns ``None`` for any of:

    - non-warning events (``event != "warning"``);
    - warnings with an unrecognized ``kind`` (forward-compatible degrade);
    - malformed payloads (missing ``signal_id`` or ``severity``).
    """
    if event.event != "warning":
        return None
    payload = event.data or {}
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in _KNOWN_KIND_FIELDS:
        return None
    signal_id = payload.get("signal_id")
    severity = payload.get("severity")
    if not isinstance(signal_id, str) or not isinstance(severity, str):
        return None
    fields = _KNOWN_KIND_FIELDS[kind]
    data: dict[str, Any] = {}
    for field_name in fields:
        if field_name in payload:
            data[field_name] = payload[field_name]
    return {
        "kind": kind,
        "signal_id": signal_id,
        "severity": severity,
        "data": data,
    }


__all__ = ["project_warning_event"]
