"""Backoff jitter — decorrelate retry waits to avoid a thundering herd (F1).

A fixed ``2^n`` schedule makes every client that hit the same transient 429/5xx
wait the *identical* delay and retry in lockstep — a correlated spike that keeps
tripping the provider (worst under concurrent batch items sharing one rate limit).
Adding jitter spreads those retries out.

The jitter is **additive-only** (``delay + rand·ratio·delay``, never below
``delay``): a server-directed ``Retry-After`` must still be honored, so we only
ever wait *longer*, and concurrent clients that computed the same delay
de-correlate across a ``ratio`` window. This matches the pattern hermes-agent and
openclaude ship. ``ratio`` 0.25 → up to +25%.
"""

from __future__ import annotations

import asyncio
import random
from typing import Callable

DEFAULT_JITTER_RATIO = 0.25

# Module-level RNG seam: returns a float in [0, 1). Patch this in tests
# (``monkeypatch.setattr(backoff, "_rand", lambda: 0.0)``) to make jitter
# deterministic without touching the global ``random`` state.
_rand = random.random


def jittered_delay(delay: float, *, ratio: float = DEFAULT_JITTER_RATIO) -> float:
    """Return ``delay`` plus additive jitter in ``[0, ratio*delay]``.

    ``delay <= 0`` returns ``0.0`` (no wait, nothing to spread). ``ratio`` is
    clamped to ``>= 0`` so the result is never below ``delay`` — keeping any
    ``Retry-After`` the delay already encodes intact.
    """
    if delay <= 0:
        return 0.0
    return delay + max(0.0, ratio) * delay * _rand()


async def abort_aware_sleep(
    seconds: float,
    *,
    abort_check: Callable[[], bool] | None = None,
    poll_seconds: float = 0.1,
) -> None:
    """Sleep ``seconds``, returning early if ``abort_check`` becomes true (F5).

    A plain ``asyncio.sleep`` in a retry backoff ignores a cooperative abort until
    the whole wait elapses — a Stop during a 10s backoff waits the full 10s. Polling
    in ``poll_seconds`` slices honors the abort within one slice instead; the
    caller's next attempt then raises ``AbortRequested`` promptly. ``abort_check``
    ``None`` (or ``seconds <= 0``) degrades to a single ``asyncio.sleep``.
    """
    if seconds <= 0:
        return
    if abort_check is None:
        await asyncio.sleep(seconds)
        return
    remaining = seconds
    step = max(0.0, poll_seconds)
    while remaining > 0:
        if abort_check():
            return
        slice_seconds = min(step, remaining) if step > 0 else remaining
        await asyncio.sleep(slice_seconds)
        remaining -= slice_seconds


__all__ = ["DEFAULT_JITTER_RATIO", "abort_aware_sleep", "jittered_delay"]
