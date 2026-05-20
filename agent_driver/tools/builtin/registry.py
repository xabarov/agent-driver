"""Default built-in tool registration for runtime bootstrapping."""

from __future__ import annotations

from typing import Any

from agent_driver.code_agent.backends.base import PythonExecutorBackend
from agent_driver.tools.builtin.agent import register_agent_tools
from agent_driver.tools.builtin.automation import register_automation_tools
from agent_driver.tools.builtin.brief import register_brief_tools
from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.builtin.lsp import register_lsp_tools
from agent_driver.tools.builtin.mcp import register_mcp_tools
from agent_driver.tools.builtin.messaging import register_messaging_tools
from agent_driver.tools.builtin.powershell import register_powershell_tools
from agent_driver.tools.builtin.python import register_python_tool
from agent_driver.tools.builtin.shell import register_shell_tools
from agent_driver.tools.builtin.skills import register_skill_tools
from agent_driver.tools.builtin.tasking import register_tasking_tools
from agent_driver.tools.builtin.tool_search import register_tool_search_tools
from agent_driver.tools.builtin.web import register_web_tools
from agent_driver.tools.builtin.worktree import register_worktree_tools
from agent_driver.tools.registry import ToolRegistry


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    python_backend: PythonExecutorBackend | None = None,
    python_settings: Any | None = None,
) -> None:
    """Register default built-in tools into provided registry."""
    register_filesystem_tools(registry)
    register_web_tools(registry)
    register_lsp_tools(registry)
    register_shell_tools(registry)
    register_powershell_tools(registry)
    register_tasking_tools(registry)
    register_mcp_tools(registry)
    register_skill_tools(registry)
    register_tool_search_tools(registry)
    register_brief_tools(registry)
    register_agent_tools(registry)
    register_messaging_tools(registry)
    register_worktree_tools(registry)
    register_automation_tools(registry)
    if python_backend is not None and python_settings is not None and python_settings.enabled:
        register_python_tool(registry, backend=python_backend, settings=python_settings)


__all__ = ["register_builtin_tools"]
