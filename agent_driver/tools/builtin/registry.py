"""Default built-in tool registration for runtime bootstrapping."""

from __future__ import annotations

from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.builtin.mcp import register_mcp_tools
from agent_driver.tools.builtin.shell import register_shell_tools
from agent_driver.tools.builtin.tasking import register_tasking_tools
from agent_driver.tools.builtin.web import register_web_tools
from agent_driver.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register default built-in tools into provided registry."""
    register_filesystem_tools(registry)
    register_web_tools(registry)
    register_shell_tools(registry)
    register_tasking_tools(registry)
    register_mcp_tools(registry)


__all__ = ["register_builtin_tools"]
