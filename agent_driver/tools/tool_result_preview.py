"""Reusable tool-result preview/paging primitives (epic 029, phase A).

Both hermes (`tools/tool_result_storage.py`) and openclaude
(`utils/toolResultStorage.ts`) converge on the same shape for a large tool
result: keep a bounded, human-readable preview in context plus an explicit
pointer to fetch the rest, instead of silently truncating and dropping the
tail. These are the pure, host-agnostic pieces of that pattern:

- :func:`safe_preview` — codepoint-safe truncation cut at a line boundary with
  an explicit ``[…omitted N characters…]`` marker. Byte-slicing mid-codepoint
  produces mojibake on non-ASCII (RU) text — a repeated bug class in openclaude
  (grapheme-boundary fixes) — so we slice on characters and prefer newlines.
- :func:`empty_result_marker` — an explicit "no output" sentinel so a model
  never ends a turn on a bare metadata tail (both references).
- :func:`persisted_output_envelope` — the ``<persisted-output>`` block a host
  emits when it stored the full result elsewhere and offers a re-read tool.

No sandbox/FS coupling and no per-turn budget machinery here — those are the
host-owned (store) and large/deferred (budget, cache-safe cleanup) parts of the
epic. A host wires these into its own tool payloads.
"""

from __future__ import annotations

_OMISSION_TEMPLATE = "\n[…опущено {n} символов; см. инструкцию ниже, как дочитать…]"
_EMPTY_MARKER = "(инструмент отработал без вывода)"


def safe_preview(text: str, *, max_chars: int, prefer_newline: bool = True) -> str:
    """Return ``text`` bounded to ~``max_chars`` codepoints with an omission marker.

    Cuts on a character boundary (never mid-codepoint) and, when
    ``prefer_newline``, backs up to the last newline within the kept window so a
    preview ends on a clean line. Appends an explicit marker naming how many
    characters were dropped; short text is returned unchanged with no marker.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    kept = text[:max_chars]
    if prefer_newline:
        newline = kept.rfind("\n")
        # Honor the last newline in the window (cut on a clean line, openclaude
        # head-cut), unless it sits so early it would collapse the preview to
        # almost nothing (a very long first line).
        if newline >= max_chars // 4:
            kept = kept[:newline]
    kept = kept.rstrip()
    omitted = len(text) - len(kept)
    return kept + _OMISSION_TEMPLATE.format(n=omitted)


def is_truncated(text: str, *, max_chars: int) -> bool:
    """Whether :func:`safe_preview` would drop content for this text."""
    return max_chars > 0 and len(text) > max_chars


def empty_result_marker() -> str:
    """Explicit sentinel for a tool that produced no output."""
    return _EMPTY_MARKER


def persisted_output_envelope(
    *, preview: str, reread_hint: str, result_key: str | None = None
) -> str:
    """Wrap a preview + re-read instruction as a ``<persisted-output>`` block.

    ``reread_hint`` tells the model exactly how to fetch the omitted tail (e.g.
    "call get_meeting_fragments(meeting_id=X, query=Y) for the full segment").
    ``result_key`` optionally identifies the stored result for a generic
    read-back tool.
    """
    key_line = f" key={result_key}" if result_key else ""
    return (
        f"<persisted-output{key_line}>\n"
        f"{preview}\n"
        f"[дочитать полностью: {reread_hint}]\n"
        f"</persisted-output>"
    )


__all__ = [
    "empty_result_marker",
    "is_truncated",
    "persisted_output_envelope",
    "safe_preview",
]
