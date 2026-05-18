"""PTL retry helpers for compaction prompt fitting."""

from __future__ import annotations


def ptl_retry_drop_oldest_groups(
    *,
    groups: list[str],
    max_chars: int,
) -> tuple[list[str], list[str]]:
    """Drop oldest groups until prompt fits max char budget."""
    kept = list(groups)
    dropped: list[str] = []
    while kept and sum(len(item) for item in kept) > max_chars:
        dropped.append(kept.pop(0))
    return kept, dropped


__all__ = ["ptl_retry_drop_oldest_groups"]
