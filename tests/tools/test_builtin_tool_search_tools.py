"""Tests for built-in tool registry search/discovery tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.registry import register_builtin_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_tool_search_filters_by_query_and_risk() -> None:
    """tool_search should filter by case-insensitive query and risk."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tool = registry.get("tool_search")
    assert tool is not None
    out = await tool.handler({"query": "mcp", "risk": "medium"})
    rows = out["tools"]
    assert rows
    assert all("mcp" in row["name"] for row in rows)
    assert all(row["risk"] == "medium" for row in rows)


@pytest.mark.asyncio
async def test_tool_search_can_include_schema_fields() -> None:
    """tool_search should include schemas when include_schemas is enabled."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tool = registry.get("tool_search")
    assert tool is not None
    out = await tool.handler({"query": "read_file", "include_schemas": True})
    rows = out["tools"]
    assert rows
    row = rows[0]
    assert row["name"] == "read_file"
    assert isinstance(row["args_schema"], dict)
