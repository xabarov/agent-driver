"""Inline chain-of-thought hygiene for assistant-produced text (epic 043 A).

Reasoning models served without a reasoning parser (deepseek-r1 / qwen3 via a
plain OpenAI-compatible endpoint, ollama raw mode) emit their chain of thought
inline as a leading ``<think>...</think>`` block in ``content`` instead of the
separate ``reasoning_content`` channel. Persisting that block into replayable
history is the transcript-poisoning class from the reference incident
(hermes cf0c42fa0): an assistant turn exposing its own CoT reads as a
prefill/reasoning-injection to provider-side classifiers and can permanently
brick the session with empty responses.

Only the LEADING block is stripped (after optional whitespace) — that is the
wire shape reasoning models actually produce. A ``<think>`` appearing mid-text
(e.g. quoted inside a code fence) is left alone. An unterminated leading block
means the model never left the reasoning phase — everything is CoT and the
cleaned text is empty (the empty-answer recovery ladder owns that case).

The provider reasoning echo channel (``reasoning_details`` / ``reasoning``
message metadata, capability-gated and covered by ``strip_reasoning_echo``)
is deliberately out of scope: it is a provider replay contract, not content.
"""

from __future__ import annotations

import re

_LEADING_THINK_RE = re.compile(
    r"\A\s*<(think|thinking)>(?P<body>.*?)</\1>\s*",
    re.IGNORECASE | re.DOTALL,
)
_UNTERMINATED_THINK_RE = re.compile(
    r"\A\s*<(think|thinking)>",
    re.IGNORECASE,
)


def strip_leading_think_block(text: str) -> tuple[str, int]:
    """Return ``(cleaned, stripped_chars)`` with leading CoT blocks removed.

    ``stripped_chars`` is 0 when the text carried no leading block, so callers
    can gate metadata/observability records on it without re-comparing strings.
    """
    if not text or not text.lstrip().startswith("<"):
        return text, 0
    cleaned = text
    while True:
        match = _LEADING_THINK_RE.match(cleaned)
        if match is None:
            break
        cleaned = cleaned[match.end() :]
    if _UNTERMINATED_THINK_RE.match(cleaned):
        # Opened, never closed: the whole remainder is reasoning.
        cleaned = ""
    stripped = len(text) - len(cleaned)
    return cleaned, stripped if stripped > 0 else 0


__all__ = ["strip_leading_think_block"]
