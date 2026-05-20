"""Streaming helpers for incremental markdown-like rendering."""

from __future__ import annotations


class MarkdownStreamBuffer:
    """Collect token deltas and expose stable lines plus trailing fragment."""

    def __init__(self) -> None:
        self._text = ""
        self._stable_len = 0

    def append(self, delta: str) -> tuple[str, str]:
        """Append token delta and return (stable_chunk, tail_fragment)."""
        if not delta:
            return ("", self.tail_fragment)
        self._text += delta
        last_newline = self._text.rfind("\n")
        if last_newline < 0:
            return ("", self.tail_fragment)
        stable_end = last_newline + 1
        if stable_end <= self._stable_len:
            return ("", self.tail_fragment)
        stable_chunk = self._text[self._stable_len : stable_end]
        self._stable_len = stable_end
        return (stable_chunk, self.tail_fragment)

    @property
    def tail_fragment(self) -> str:
        return self._text[self._stable_len :]

    @property
    def full_text(self) -> str:
        return self._text

    def finalize(self) -> str:
        """Return unflushed tail and mark all text consumed."""
        tail = self.tail_fragment
        self._stable_len = len(self._text)
        return tail


__all__ = ["MarkdownStreamBuffer"]
