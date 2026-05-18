"""Default built-in tool registration for runtime bootstrapping."""

from __future__ import annotations

from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.builtin.web import register_web_tools
from agent_driver.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register default built-in tools into provided registry."""
    register_filesystem_tools(registry)
    register_web_tools(registry)


__all__ = ["register_builtin_tools"]
