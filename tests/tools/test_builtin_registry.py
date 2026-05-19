"""Tests for default built-in registry wiring."""

from agent_driver.tools import register_planning_tool
from agent_driver.tools.builtin.registry import register_builtin_tools
from agent_driver.tools.registry import ToolRegistry


def test_register_builtin_tools_populates_registry() -> None:
    """register_builtin_tools should install first-wave filesystem tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    names = registry.list_names()
    assert "read_file" in names
    assert "glob_search" in names
    assert "grep_search" in names
    assert "file_write" in names
    assert "file_edit" in names
    assert "notebook_edit" in names
    assert "web_fetch" in names
    assert "web_search" in names
    assert "bash" in names
    assert "task_create" in names
    assert "task_get" in names
    assert "task_list" in names
    assert "task_update" in names
    assert "task_output" in names
    assert "mcp_tool" in names
    assert "mcp_list_resources" in names
    assert "mcp_read_resource" in names
    assert "mcp_auth" in names
    assert "skill_tool" in names
    assert "tool_search" in names
    assert "brief_tool" in names
    assert "todo_write" in names
    assert "ask_user_question" in names
    assert "enter_plan_mode" in names
    assert "exit_plan_mode_v2" in names
