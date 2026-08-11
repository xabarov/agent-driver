"""Live coordination events — see a fan-out unfold as it runs (coordination observability).

`describe()` explains a coordination result *after* it finishes. For a long fan-out you also
want to see it *while* it runs — which worker started, which just came back, which is
retrying, when a phase flips. `run_subagent_group` / `run_coordinator` / `run_deep_agent`
take an optional ``on_event`` observer that receives a :class:`CoordinationEvent` at each
lifecycle point, so a consumer can stream progress to a log, a UI, or a metric.

    from agent_driver.sdk import log_coordination_events, run_coordinator
    await run_coordinator(parent, phases, on_event=log_coordination_events())

The observer is called synchronously on the event loop and **must not** break the run — an
observer that raises is swallowed (logged at debug). Events carry the raw
:class:`SubagentResult` on completion so a consumer can `describe_subagent`/`digest_subagent`
it; this module stays free of the higher-level render helpers to avoid an import cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from agent_driver.sdk.subagent import SubagentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CoordinationEvent:
    """One live event from a coordination run.

    ``kind`` is the discriminator; the other fields are populated as relevant:

    - ``group_started`` / ``group_completed`` — ``phase`` (if any), ``total`` children.
    - ``child_started`` — ``phase``, ``agent_type``, ``index``, ``total``.
    - ``child_retrying`` — ``phase``, ``agent_type``, ``index``, ``detail`` (attempt).
    - ``child_completed`` — ``phase``, ``agent_type``, ``index``, ``status``, ``result``.
    - ``phase_started`` / ``phase_completed`` — ``phase``, ``detail`` (coordinator).
    - ``plan_ready`` — ``total`` subtasks, ``detail`` (deep-agent).
    - ``synthesis_started`` / ``synthesis_completed`` — ``agent_type``, ``status``,
      ``result`` (deep-agent).
    """

    kind: str
    phase: str | None = None
    agent_type: str | None = None
    index: int | None = None
    total: int | None = None
    status: str | None = None
    result: SubagentResult | None = None
    detail: str | None = None


# An observer receives each event; it is called on the event loop and must not block long.
CoordinationObserver = Callable[[CoordinationEvent], None]


def emit_event(on_event: CoordinationObserver | None, kind: str, **fields: object) -> None:
    """Deliver one event to ``on_event`` (a no-op if None); never raises into the run."""
    if on_event is None:
        return
    try:
        on_event(CoordinationEvent(kind=kind, **fields))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — an observer must never break the coordination run
        logger.debug("coordination observer raised", exc_info=True)


__all__ = ["CoordinationEvent", "CoordinationObserver", "emit_event"]
