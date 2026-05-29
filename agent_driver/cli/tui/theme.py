"""Semantic style tokens for chat terminal UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatTheme:
    """Semantic color palette for rich chat rendering."""

    text: str = "white"
    subtle: str = "bright_black"
    brand: str = "bright_blue"
    accent: str = "cyan"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"
    prompt_border: str = "grey62"
    user_bg: str = "grey23"


DEFAULT_THEME = ChatTheme()

__all__ = ["ChatTheme", "DEFAULT_THEME"]
