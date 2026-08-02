"""Cooperative tool-cancellation contract (U4 / epic 052).

A running tool handler cannot see the run's process-local ``RunAbortHandle``
directly. When the runtime plumbs an abort handle into the governed executor,
each handler invocation is wrapped in a ``tool_cancellation_scope`` (see
``agent_driver.tools.context``) carrying a :class:`ToolCancellation` the handler
can consult via ``current_tool_cancellation()`` to cooperatively stop its own
work — cancel a socket, close a browser, abandon a long query — and to surface
run/call/attempt identity so the host can correlate the cancellation with its
own job.

Domain-neutral by design: this module does not import the runtime abort
primitive; the executor adapts ``RunAbortHandle.is_aborted`` into the
``_check`` predicate. Handlers that never consult the token incur no overhead
and keep the plain ``Callable[[dict], Awaitable[dict]]`` signature.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass


class ToolCancelledError(Exception):
    """Raised by :meth:`ToolCancellation.raise_if_cancelled` when aborted."""


@dataclass(frozen=True, slots=True)
class ToolCancellation:
    """Read-only cooperative-cancellation signal for one tool handler call.

    Attributes:
        run_id: the enclosing run's identifier.
        tool_call_id: the logical planned call being executed.
        attempt_id: this execution attempt of the call.
        deadline_seconds: optional bounded deadline the host may honour when
            cancelling its own work (``None`` when the run set no deadline).
        _check: predicate returning True once the run has been aborted.
    """

    run_id: str | None
    tool_call_id: str | None
    attempt_id: str | None
    deadline_seconds: float | None
    _check: Callable[[], bool]

    @property
    def is_cancelled(self) -> bool:
        """True once the enclosing run has been aborted."""
        try:
            return bool(self._check())
        except Exception:  # pragma: no cover - defensive: never crash a handler
            return False

    def raise_if_cancelled(self) -> None:
        """Raise :class:`ToolCancelledError` if the run has been aborted."""
        if self.is_cancelled:
            raise ToolCancelledError(
                f"tool call '{self.tool_call_id}' cancelled (run '{self.run_id}')"
            )

    async def wait_cancelled(self, *, poll_interval_s: float = 0.05) -> None:
        """Return once cancellation is observed.

        Cooperative poll loop (mirrors ``RunAbortHandle.wait_aborted``): a
        handler awaiting a long external operation can race it against this to
        return promptly when the run is stopped.
        """
        while not self.is_cancelled:
            await asyncio.sleep(poll_interval_s)


__all__ = ["ToolCancellation", "ToolCancelledError"]
