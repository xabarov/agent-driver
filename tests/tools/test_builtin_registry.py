"""Tests for default built-in registry wiring."""

from agent_driver.tools import register_planning_tool
from agent_driver.tools.builtin.registry import register_builtin_tools
from agent_driver.tools.registry import ToolRegistry
from tests.tools._builtin_expected import EXPECTED_BUILTIN_TOOL_NAMES


def test_register_builtin_tools_populates_registry() -> None:
    """register_builtin_tools should install first-wave filesystem tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    names = registry.list_names()
    assert EXPECTED_BUILTIN_TOOL_NAMES.issubset(set(names))
