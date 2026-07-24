"""Adaptive tool-deferral threshold (epic 033 phase A).

Deferral marking (``ToolManifest.should_defer`` / ``is_deferred``) and schema
omission already exist (Phase 12 H21); a deferred tool is dropped from the prompt
and rediscovered via ``tool_search`` / the defer primer. What was missing is the
hermes ``should_activate`` gate: deferral should not fire *unconditionally* — a
handful of small deferrable schemas cost less inline than the ``tool_search``
round-trip they'd force. So ``should_defer`` becomes a *candidate* mark, and this
policy decides per assembly whether the candidates are actually worth deferring:
only when their combined schema size crosses a fraction of the model's context
window (hermes default 10%, or a fixed 20K-token cliff when the window is unknown).

Below the threshold the candidates are force-surfaced (treated as non-deferred for
this step); at or above it they defer as before. ``mode="on"`` keeps the historical
always-defer behaviour; ``mode="off"`` disables deferral entirely.

Reference: hermes ``tools/tool_search.py::should_activate`` (threshold_pct=10,
CHARS_PER_TOKEN=4, 20K fallback cliff "above which Anthropic and OpenAI both saw
quality drops").
"""

from __future__ import annotations

import json
from typing import Any

# Roughly 4 chars per token for English + JSON tool schemas (hermes constant).
CHARS_PER_TOKEN = 4.0
# Without a known context window, defer once candidates cross this fixed cliff.
FALLBACK_ACTIVATION_TOKENS = 20_000
DEFAULT_THRESHOLD_PCT = 10.0


def estimate_schema_tokens(schemas: list[dict[str, Any]]) -> int:
    """Rough token estimate of a set of tool schemas (chars / CHARS_PER_TOKEN)."""
    total_chars = 0
    for schema in schemas:
        try:
            total_chars += len(json.dumps(schema, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            total_chars += len(str(schema))
    return int(total_chars / CHARS_PER_TOKEN)


def should_activate_deferral(
    deferrable_tokens: int,
    context_window: int | None,
    *,
    mode: str = "auto",
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> bool:
    """Decide whether deferral should fire for this assembly (hermes ``should_activate``).

    ``mode``: ``"off"`` never defers, ``"on"`` always defers (historical behaviour),
    ``"auto"`` defers only when the candidate schemas cross ``threshold_pct`` of the
    window (or the 20K-token cliff when the window is unknown).
    """
    if mode == "off":
        return False
    if deferrable_tokens <= 0:
        return False
    if mode == "on":
        return True
    # auto
    if not context_window or context_window <= 0:
        return deferrable_tokens >= FALLBACK_ACTIVATION_TOKENS
    threshold_tokens = int(context_window * (threshold_pct / 100.0))
    return deferrable_tokens >= threshold_tokens


__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_THRESHOLD_PCT",
    "FALLBACK_ACTIVATION_TOKENS",
    "estimate_schema_tokens",
    "should_activate_deferral",
]
