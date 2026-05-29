"""Built-in tool registrations for default runtime registry."""

from agent_driver.tools.builtin.mcp import register_mcp_tools
from agent_driver.tools.builtin.registry import register_builtin_tools

__all__ = ["register_builtin_tools", "register_mcp_tools"]
