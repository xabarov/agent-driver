"""Shared runtime event emission helpers for step modules."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.types import EventSpec, RunContext

logger = logging.getLogger(__name__)


def emit_step_event(
    host: Any,
    context: RunContext,
    *,
    event_type: RuntimeEventType,
    payload: dict[str, object] | None = None,
) -> None:
    """Emit a runtime event for the current run/attempt."""
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=event_type,
            payload=payload,
        )
    )


class stage_wait_heartbeat:
    """Emit periodic liveness events while a long stage is still awaited.

    Epic 025 («нет немой длинной стадии»; hermes ``_emit_wait_notice`` /
    gateway heartbeat): a provider or tool wait longer than ``interval``
    seconds emits an info-severity WARNING with ``signal_id
    stage_wait_heartbeat`` and ``elapsed_ms``, repeating every ``interval``
    until the awaited stage returns. The host renders the latest heartbeat as
    a live «still working — Ns» label instead of a frozen caption.

    ``interval=None``/``0`` disables (no task spawned). Usage::

        async with stage_wait_heartbeat(host, context, stage="llm_completion",
                                        interval=10.0):
            response = await provider.complete(request)
    """

    def __init__(
        self,
        host: Any,
        context: RunContext,
        *,
        stage: str,
        interval: float | None,
    ) -> None:
        self._host = host
        self._context = context
        self._stage = stage
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def _beat(self) -> None:
        started = time.monotonic()
        while True:
            await asyncio.sleep(self._interval or 0)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            try:
                emit_step_event(
                    self._host,
                    self._context,
                    event_type=RuntimeEventType.WARNING,
                    payload={
                        "warning": (
                            f"Stage '{self._stage}' still running "
                            f"after {elapsed_ms / 1000:.0f}s."
                        ),
                        "signal_id": "stage_wait_heartbeat",
                        "severity": "info",
                        "stage": self._stage,
                        "elapsed_ms": elapsed_ms,
                    },
                )
            except Exception:  # pragma: no cover - liveness must never break a run
                logger.exception("stage heartbeat emit failed; stopping heartbeat")
                return

    async def __aenter__(self) -> "stage_wait_heartbeat":
        if self._interval is not None and self._interval > 0:
            self._task = asyncio.create_task(self._beat())
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # pylint: disable=broad-exception-caught
                pass


__all__ = ["emit_step_event", "stage_wait_heartbeat"]
