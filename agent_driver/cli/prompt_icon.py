"""Terminal prompt icon animation helpers for chat mode."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress


_RESET = "\033[0m"
_CLEAR_LINE = "\033[2K"

_SPINNER_FRAMES = (
    ("◜", "38;2;88;231;255"),
    ("◠", "38;2;34;211;255"),
    ("◝", "38;2;0;171;255"),
    ("◞", "38;2;0;121;255"),
    ("◡", "38;2;0;91;230"),
    ("◟", "38;2;28;155;255"),
)


def prompt_spinner_frame(index: int, *, label: str = "agent") -> str:
    """Return one ANSI-colored Agent Driver spinner frame."""
    glyph, color = _SPINNER_FRAMES[index % len(_SPINNER_FRAMES)]
    return f"\r\033[{color}m{glyph}{_RESET} {label}> thinking..."


class PromptSpinner:
    """Small async terminal spinner that writes frames until stopped."""

    def __init__(
        self,
        *,
        output: Callable[[str], None],
        enabled: bool,
        interval_seconds: float = 0.12,
        label: str = "agent",
    ) -> None:
        self._output = output
        self._enabled = enabled
        self._interval_seconds = interval_seconds
        self._label = label
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self) -> None:
        """Start animating if enabled and not already running."""
        if not self._enabled or self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._output(prompt_spinner_frame(0, label=self._label))
        self._task = asyncio.create_task(self._run(start_index=1))

    async def stop(self, *, clear: bool = True) -> None:
        """Stop animating and optionally clear the current spinner line."""
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop_event = None
        if clear:
            self._output(f"\r{_CLEAR_LINE}")

    async def _run(self, *, start_index: int) -> None:
        assert self._stop_event is not None
        index = start_index
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                self._output(prompt_spinner_frame(index, label=self._label))
                index += 1
                continue


__all__ = ["PromptSpinner", "prompt_spinner_frame"]
