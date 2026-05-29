"""Detect assistant answers that intend to continue instead of finish."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONTINUATION_PATTERNS = (
    re.compile(
        r"\b(next step is to|moving on to|now i(?:'ll| will)|i will now|let me)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(следующ(?:ий|им)\s+(?:шаг|действи\w*)\s+(?:это|является|будет)|"
        r"теперь\s+(?:я\s+)?(?:буду|нужно|необходимо)|"
        r"далее\s+(?:я\s+)?(?:буду|нужно|необходимо)|"
        r"перехожу\s+к)\b",
        re.IGNORECASE,
    ),
)
_COMPLETION_MARKERS = re.compile(
    r"\b(done|finished|completed|complete|all set|that's all|готово|завершено|выполнено)\b",
    re.IGNORECASE,
)
_UNFINISHED_SUFFIXES = (
    re.compile(r"\b(and|with|the|to|of|for|in|on|that|which)\s*$", re.IGNORECASE),
    re.compile(r"\b(и|с|для|в|на|что|котор(?:ый|ая|ое|ые)|следующ(?:ий|ая|ее))\s*$", re.IGNORECASE),
    re.compile(r"[,;:]\s*$"),
)


@dataclass(frozen=True, slots=True)
class ContinuationIntent:
    """Result of continuation intent analysis."""

    should_continue: bool
    reason: str | None = None


def analyze_continuation_intent(text: str) -> ContinuationIntent:
    """Return whether final assistant text looks like unfinished progress."""
    stripped = text.strip()
    if not stripped:
        return ContinuationIntent(False)
    if stripped.count("```") % 2:
        return ContinuationIntent(True, "unclosed_code_block")
    if any(pattern.search(stripped) for pattern in _UNFINISHED_SUFFIXES):
        return ContinuationIntent(True, "unfinished_suffix")

    late = stripped[-240:]
    for pattern in _CONTINUATION_PATTERNS:
        match = pattern.search(late)
        if not match:
            continue
        after = late[match.end() :]
        if not _COMPLETION_MARKERS.search(after):
            return ContinuationIntent(True, "continuation_signal")
    return ContinuationIntent(False)


__all__ = ["ContinuationIntent", "analyze_continuation_intent"]
