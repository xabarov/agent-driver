"""Bottom status spinner for rich chat mode."""

from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
import time

from agent_driver.cli.tui.glyphs import DOT


@dataclass(slots=True)
class StatusSpinner:
    """Status spinner backed by Rich console.status when available."""

    output: Callable[[str], None]
    enabled: bool
    console: Any | None = None
    label: str = "Pondering..."
    interval_seconds: float = 0.12
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _stop_event: asyncio.Event | None = field(default=None, init=False)
    _started_at: float | None = field(default=None, init=False)
    _token_count: int = field(default=0, init=False)
    _status: Any | None = field(default=None, init=False)

    def increment_tokens(self, delta_text: str) -> None:
        if delta_text:
            self._token_count += len(delta_text)

    def set_label(self, value: str) -> None:
        self.label = value
        self._update_status()

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._started_at = time.monotonic()
        self._token_count = 0
        self._open_status()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop_event = None
        self._close_status()

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            self._update_status()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                continue

    def _render_line(self) -> str:
        elapsed = 0 if self._started_at is None else int(time.monotonic() - self._started_at)
        return f"{self.label} ({DOT} thinking {DOT} {elapsed}s {DOT} ↓ {self._token_count} chars)"

    def _open_status(self) -> None:
        if self.console is None:
            return
        self._status = self.console.status(self._render_line(), spinner="dots")
        self._status.start()

    def _update_status(self) -> None:
        if self._status is None:
            return
        self._status.update(self._render_line())

    def _close_status(self) -> None:
        if self._status is None:
            return
        self._status.stop()
        self._status = None


__all__ = ["StatusSpinner"]
