"""Type aliases for tool handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

__all__ = ["ToolHandler"]
