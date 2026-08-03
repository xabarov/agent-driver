"""PTL retry helpers for compaction prompt fitting."""

from __future__ import annotations

from collections.abc import Collection


def ptl_retry_drop_oldest_groups(
    *,
    groups: list[str],
    max_chars: int,
    protected_indexes: Collection[int] = (),
) -> tuple[list[str], list[str]]:
    """Drop whole oldest unprotected groups until the prompt fits.

    Protected groups remain atomic even when they alone exceed ``max_chars``.
    The caller can then report the bounded semantic overrun explicitly instead
    of slicing an authoritative contract or material evidence packet.
    """
    protected = {index for index in protected_indexes if 0 <= index < len(groups)}
    kept_indexes = list(range(len(groups)))
    dropped: list[str] = []
    total_chars = sum(len(item) for item in groups)
    for index, group in enumerate(groups):
        if total_chars <= max_chars:
            break
        if index in protected:
            continue
        kept_indexes.remove(index)
        dropped.append(group)
        total_chars -= len(group)
    return [groups[index] for index in kept_indexes], dropped


__all__ = ["ptl_retry_drop_oldest_groups"]
