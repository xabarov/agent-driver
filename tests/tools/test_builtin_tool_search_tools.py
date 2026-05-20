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


@pytest.mark.asyncio
async def test_tool_search_requires_non_empty_query() -> None:
    """tool_search should fail fast on empty query."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tool = registry.get("tool_search")
    assert tool is not None
    with pytest.raises(ValueError, match="query is required"):
        await tool.handler({"query": ""})


@pytest.mark.asyncio
async def test_tool_search_reports_truncated_metadata() -> None:
    """tool_search should expose cap metadata when max_results is reached."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tool = registry.get("tool_search")
    assert tool is not None
    out = await tool.handler({"query": "tool", "max_results": 1})
    assert out["returned_count"] == 1
    assert out["truncated"] is True
    assert out["more_available"] is True
