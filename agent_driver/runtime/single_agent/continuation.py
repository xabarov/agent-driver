"""Detect assistant answers that intend to continue instead of finish."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONTINUATION_PATTERNS = (
    re.compile(
        r"\b(next step is to|moving on to|now i(?:'ll| will)|i will now|"
        r"i am now|i'm now|let me)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(следующ(?:ий|им)\s+(?:шаг\w*|действи\w*)"
        r"(?:\s+(?:это|является|будет)|\s*[—\-:]?)|"
        r"теперь\s+(?:я\s+)?"
        r"(?:буду|нужно|необходимо|работаю|начинаю|приступаю|перехожу|"
        r"структурирую|готовлю)|"
        r"сейчас\s+(?:я\s+)?"
        r"(?:буду|работаю|начинаю|приступаю|перехожу|структурирую|готовлю)|"
        r"далее\s+(?:я\s+)?"
        r"(?:буду|нужно|необходимо|работаю|начинаю|приступаю|перехожу|"
        r"структурирую|готовлю)|"
        r"(?:приступаю|начинаю|перехожу)\s+к)\b",
        re.IGNORECASE,
    ),
)
_COMPLETION_MARKERS = re.compile(
    r"\b(done|finished|completed|complete|all set|that's all|готово|завершено|выполнено)\b",
    re.IGNORECASE,
)
_UNFINISHED_SUFFIXES = (
    re.compile(r"\b(and|with|the|to|of|for|in|on|that|which)\s*$", re.IGNORECASE),
    re.compile(
        r"\b(и|с|для|в|на|что|котор(?:ый|ая|ое|ые)|следующ(?:ий|ая|ее))\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"[,;:]\s*$"),
)
_TEXT_FORM_TOOL_CALL_RE = re.compile(
    r"(<\s*/?\s*tool_call\s*>|<\|python_tag\|>|"
    r"^\s*\{[\s\S]{0,400}\"name\"\s*:\s*\"[a-zA-Z0-9_]+\""
    r"[\s\S]{0,1200}\"arguments\"\s*:)",
    re.IGNORECASE,
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
    if _TEXT_FORM_TOOL_CALL_RE.search(stripped):
        return ContinuationIntent(True, "text_form_tool_call")
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
