"""Server-directed retry hints (resilience F3).

Beyond ``Retry-After`` (already honored in ``llm/base.py``), providers signal two
more things a good client obeys instead of guessing:

* **``x-should-retry``** — an explicit yes/no the OpenAI- and Anthropic-style APIs
  send: ``false`` means "don't bother, this won't clear" (skip the retry and fail
  fast instead of burning the budget), ``true`` means "safe to retry."
* **``*ratelimit*reset*`` headers** — when the current limit lifts. Waiting until
  then beats a blind exponential guess that either wastes time or retries too soon
  and re-trips the limit.

Both are provider-neutral conventions, so they live in the runtime (not an
adapter). Values are parsed defensively and the reset delay is capped so a large
reset can't stall a bounded retry loop — the loop re-reads the header next attempt.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

# Cap a reset-derived wait at the same ceiling ``Retry-After`` uses (llm/base.py),
# so an hour-long ``*-reset`` can't wedge a bounded retry loop.
RESET_DELAY_CAP_SECONDS = 32.0

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}
# Any value below this is read as relative seconds; at/above it, as an epoch stamp.
_EPOCH_THRESHOLD = 1_000_000.0


def _get(headers: Any, key: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(key)
    return value if isinstance(value, str) else None


def parse_should_retry(headers: Any) -> bool | None:
    """Return the server's ``x-should-retry`` directive, or ``None`` if unset.

    ``True`` = the server says the request is safe to retry; ``False`` = it says
    retrying won't help (fail fast); ``None`` = no opinion (use local heuristics).
    """
    raw = _get(headers, "x-should-retry")
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def _reset_value_to_seconds(raw: str, now_epoch: float) -> float | None:
    """Convert one ``*-reset`` header value to seconds-until-reset (>= 0)."""
    text = raw.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if number < 0:
            return None
        if number >= _EPOCH_THRESHOLD:  # absolute epoch timestamp
            return max(0.0, number - now_epoch)
        return number  # relative seconds
    # ISO-8601 / RFC-3339 timestamp (e.g. Anthropic's unified reset).
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - now_epoch)


def rate_limit_reset_seconds(
    headers: Any,
    *,
    now: Callable[[], float] = time.time,
    cap: float = RESET_DELAY_CAP_SECONDS,
) -> float | None:
    """Seconds until the rate limit resets, from any ``*ratelimit*reset*`` header.

    Scans every header whose name contains both ``ratelimit`` and ``reset`` and
    returns the **latest** reset (so a retry doesn't re-trip a still-limited
    dimension), capped at ``cap``. ``None`` when no parsable reset header is
    present. ``now`` is an injectable epoch clock for deterministic tests.
    """
    items = getattr(headers, "items", None)
    if items is None:
        return None
    now_epoch = now()
    latest: float | None = None
    for key, value in items():
        name = str(key).lower()
        if "ratelimit" not in name or "reset" not in name:
            continue
        if not isinstance(value, str):
            continue
        seconds = _reset_value_to_seconds(value, now_epoch)
        if seconds is None:
            continue
        latest = seconds if latest is None else max(latest, seconds)
    if latest is None:
        return None
    return min(latest, cap)


def strongest_retry_delay(
    retry_after: float | None, reset_seconds: float | None
) -> float | None:
    """Combine a ``Retry-After`` and a reset-derived wait — the longer of the two.

    Either may be ``None``; returns ``None`` only when both are. Taking the max
    honors whichever directive asks us to wait longest.
    """
    candidates = [value for value in (retry_after, reset_seconds) if value is not None]
    if not candidates:
        return None
    return max(candidates)


__all__ = [
    "RESET_DELAY_CAP_SECONDS",
    "parse_should_retry",
    "rate_limit_reset_seconds",
    "strongest_retry_delay",
]
