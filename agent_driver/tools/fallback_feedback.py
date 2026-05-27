"""Phase 13 H29.3 — structured feedback for invalid tool calls.

When the LLM emits a tool call that cannot be executed (unknown tool
name, malformed arguments, JSON parse failure), the runtime today
returns a sparse error code (``tool_not_registered`` /
``arguments_json_parse_failed`` etc.) back to the model. Open-weights
models often need a richer hint to self-correct on the next turn —
otherwise they retry the same broken call multiple times before
either giving up or coincidentally landing on a valid name.

This module provides pure helpers that produce LLM-friendly feedback
strings. The helpers are intentionally side-effect-free so callers
(executor block paths, parse-error sinks, future protocol_validate
adapter) can use them without taking on a runtime dependency.

The feedback strings are designed for embedding in the ``reason``
field of a ``BlockSpec`` or in a synthetic tool-error ChatMessage:
short, declarative, ending in an actionable instruction.

Public surface:

* :func:`closest_tool_names` — fuzzy-match a misspelled tool name
  against a list of registered names. Returns up to ``max_suggestions``
  entries ranked by similarity.
* :func:`build_unknown_tool_feedback` — full string for the
  ``tool_not_registered`` path.
* :func:`build_arguments_parse_feedback` — string for malformed JSON
  arguments.
* :func:`build_missing_tool_name_feedback` — string for the rarest
  case: the model emitted a tool-call block with no ``name`` field.

The helpers stay deterministic so they're trivial to unit-test and
safe to inline into log messages.
"""

from __future__ import annotations

from difflib import get_close_matches


_DEFAULT_MAX_SUGGESTIONS = 3
_DEFAULT_CUTOFF = 0.6  # difflib's default, kept explicit for transparency.


def closest_tool_names(
    name: str,
    available: list[str] | tuple[str, ...],
    *,
    max_suggestions: int = _DEFAULT_MAX_SUGGESTIONS,
    cutoff: float = _DEFAULT_CUTOFF,
) -> list[str]:
    """Return the closest ``max_suggestions`` names to ``name`` by fuzzy ratio.

    Empty inputs (no candidate name, no available list, or no
    candidate above the cutoff) yield an empty list. Case-insensitive
    matching — open-weights models sometimes capitalize differently
    than the registry.
    """
    if not isinstance(name, str) or not name.strip():
        return []
    if not available:
        return []
    name_lc = name.strip().lower()
    available_lc = {n.lower(): n for n in available if isinstance(n, str) and n}
    if not available_lc:
        return []
    matches_lc = get_close_matches(
        name_lc,
        list(available_lc.keys()),
        n=max_suggestions,
        cutoff=cutoff,
    )
    return [available_lc[m] for m in matches_lc]


def _format_tool_list(names: list[str] | tuple[str, ...], *, limit: int = 30) -> str:
    """Comma-separate up to ``limit`` names; append "…" when truncated."""
    items = [n for n in names if isinstance(n, str) and n][:limit]
    if not items:
        return "(none registered)"
    suffix = "" if len(names) <= limit else f", … (+{len(names) - limit} more)"
    return ", ".join(items) + suffix


def build_unknown_tool_feedback(
    tool_name: str,
    available: list[str] | tuple[str, ...],
    *,
    max_suggestions: int = _DEFAULT_MAX_SUGGESTIONS,
) -> str:
    """Build the message for an unregistered tool name.

    Format: ``"Tool 'X' is not registered. Did you mean: 'Y', 'Z'?
    Available tools: …"`` — first the closest matches (only when at
    least one passes the cutoff), then a compact catalog so the model
    has the option to pick a non-fuzzy alternative.
    """
    suggestions = closest_tool_names(
        tool_name, available, max_suggestions=max_suggestions
    )
    parts = [f"Tool '{tool_name}' is not registered."]
    if suggestions:
        quoted = ", ".join(f"'{name}'" for name in suggestions)
        parts.append(f"Did you mean: {quoted}?")
    parts.append(f"Available tools: {_format_tool_list(available)}.")
    return " ".join(parts)


def build_arguments_parse_feedback(
    tool_name: str,
    raw_arguments: str | None,
    *,
    error_detail: str | None = None,
) -> str:
    """Build the message for tool args that failed JSON parsing.

    The model sees the offending raw payload truncated so it can spot
    the syntax issue, plus a generic reminder that arguments MUST be
    a JSON object.
    """
    parts = [
        f"Arguments for tool '{tool_name}' could not be parsed as JSON.",
    ]
    if error_detail:
        parts.append(f"Parse error: {error_detail}.")
    if isinstance(raw_arguments, str) and raw_arguments:
        snippet = raw_arguments.strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        parts.append(f"Raw value seen: `{snippet}`.")
    parts.append(
        'Emit a JSON object (e.g. `{"key": "value"}`) or `{}` for no arguments.'
    )
    return " ".join(parts)


def build_missing_tool_name_feedback() -> str:
    """The model emitted a tool-call block without a ``name`` field."""
    return (
        "Tool-call block is missing a tool name. "
        'Include a "name" field naming one of the registered tools.'
    )


__all__ = [
    "closest_tool_names",
    "build_unknown_tool_feedback",
    "build_arguments_parse_feedback",
    "build_missing_tool_name_feedback",
]
