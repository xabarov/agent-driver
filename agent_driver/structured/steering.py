"""Prototype structured steering parser."""

from __future__ import annotations

import re

from agent_driver.contracts import ControlKind, ControlPriority, ControlRequest
from agent_driver.contracts.control import BusyPolicy, control_request_for_message


_MODEL_PATTERNS = (
    re.compile(r"(?:switch|set|change)\s+(?:to\s+)?(?:model\s+)?(?P<model>[\w./:-]+)", re.I),
    re.compile(r"(?:model|модель)\s*[:=]\s*(?P<model>[\w./:-]+)", re.I),
)


def parse_steering_text(
    text: str,
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    agent_id: str | None = None,
    priority: ControlPriority = ControlPriority.NEXT,
    busy_policy: BusyPolicy = BusyPolicy.QUEUE,
) -> ControlRequest:
    """Parse a small natural-language steering command into ``ControlRequest``.

    This deterministic prototype covers the first high-value control shapes.
    Instructor can later replace only this parser boundary with an LLM-backed
    Pydantic extraction step.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("steering text must be non-empty")
    lowered = cleaned.casefold()
    for pattern in _MODEL_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            return ControlRequest(
                kind=ControlKind.SET_MODEL,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
                priority=priority,
                payload={"model": match.group("model")},
                source="structured_parser",
            )
    if lowered in {"stop", "cancel", "interrupt", "pause", "останови", "стоп"}:
        return ControlRequest(
            kind=ControlKind.INTERRUPT,
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
            priority=ControlPriority.NOW,
            payload={"reason": "user_requested_interrupt", "message": cleaned},
            source="structured_parser",
        )
    # A bare message (no explicit model/stop verb) is routed by the session's busy
    # policy: interrupt (hard redirect) / queue (next turn) / steer (soft fold).
    return control_request_for_message(
        cleaned,
        policy=busy_policy,
        run_id=run_id,
        thread_id=thread_id,
        agent_id=agent_id,
        source="structured_parser",
    )


__all__ = ["parse_steering_text"]
