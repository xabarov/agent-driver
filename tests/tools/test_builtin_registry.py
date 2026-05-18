"""Tests for default built-in registry wiring."""

from agent_driver.tools.builtin.registry import register_builtin_tools
from agent_driver.tools.registry import ToolRegistry


def test_register_builtin_tools_populates_registry() -> None:
    """register_builtin_tools should install first-wave filesystem tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    names = registry.list_names()
    assert "read_file" in names
    assert "glob_search" in names
    assert "grep_search" in names
    assert "web_fetch" in names
    assert "web_search" in names
