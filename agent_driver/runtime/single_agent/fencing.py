"""Attempt-epoch result fencing primitives (F1 / U4 — epic 052).

A tool result can be *attributed* to the execution attempt that produced it by
stamping the run's ``attempt_epoch`` (see ``RunContext.attempt_epoch``) into the
result metadata under a reserved key. A straggler result carrying an epoch older
than the run's current epoch belongs to a superseded attempt and must not be
folded back in — :func:`is_stale_attempt` is the decision, and the U4 fencing
step enforces it (drops the result + emits an event) on top of this foundation.

Kept dependency-light: the stamp lives in the reserved ``_ad_`` metadata
namespace (shared with the U2 gate provenance) so model/tool output can neither
forge nor overwrite it.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.validation import RESERVED_METADATA_PREFIX

RESERVED_ATTEMPT_EPOCH_KEY = f"{RESERVED_METADATA_PREFIX}attempt_epoch"


def stamp_attempt_epoch(metadata: dict[str, Any] | None, epoch: int) -> dict[str, Any]:
    """Return ``metadata`` with the attempt epoch stamped under the reserved key.

    Only stamps when ``epoch > 0`` — a fresh run (epoch 0) needs no attribution
    and stays byte-identical to the pre-F1 metadata, so existing behaviour and
    tests are untouched.
    """
    base = dict(metadata or {})
    if epoch > 0:
        base[RESERVED_ATTEMPT_EPOCH_KEY] = int(epoch)
    return base


def attempt_epoch_of(metadata: dict[str, Any] | None) -> int | None:
    """Return the stamped attempt epoch of a result, or None when unstamped."""
    if not metadata:
        return None
    value = metadata.get(RESERVED_ATTEMPT_EPOCH_KEY)
    return int(value) if isinstance(value, int) else None


def is_stale_attempt(result_epoch: int | None, current_epoch: int) -> bool:
    """True when a result was produced under a superseded attempt.

    A result with no stamp (``None``) is treated as current (not stale) — it
    predates F1 attribution and there is nothing to fence it against.
    """
    if result_epoch is None:
        return False
    return int(result_epoch) < int(current_epoch)


__all__ = [
    "RESERVED_ATTEMPT_EPOCH_KEY",
    "attempt_epoch_of",
    "is_stale_attempt",
    "stamp_attempt_epoch",
]
