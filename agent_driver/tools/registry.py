"""Tool registry contracts and lookup helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.tools import ToolManifest

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """Immutable tuple of manifest and async handler."""

    manifest: ToolManifest
    handler: ToolHandler


class ToolRegistry:
    """In-memory registry for governed tools."""

    def __init__(self) -> None:
        self._items: dict[str, RegisteredTool] = {}

    def register(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        """Register or replace a tool by canonical manifest name."""
        self._items[manifest.name] = RegisteredTool(manifest=manifest, handler=handler)

    def get(self, tool_name: str) -> RegisteredTool | None:
        """Return registered tool by name."""
        return self._items.get(tool_name)

    def list_names(self) -> list[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._items)
