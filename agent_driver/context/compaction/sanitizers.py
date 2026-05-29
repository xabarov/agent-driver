"""Compaction input sanitizers and lightweight redaction."""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*[A-Za-z0-9_\-]{8,}"),
]


def sanitize_compaction_text(text: str) -> str:
    """Sanitize known secret-like fragments from compaction prompt text."""
    cleaned = text
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
    cleaned = cleaned.replace("attachment://", "[ATTACHMENT]")
    return cleaned


__all__ = ["sanitize_compaction_text"]
