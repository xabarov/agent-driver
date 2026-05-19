"""Tests for built-in powershell tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.powershell import register_powershell_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_powershell_tool_reports_unavailable_without_pwsh() -> None:
    """powershell_tool should fail with explicit unavailable message when pwsh missing."""
    registry = ToolRegistry()
    register_powershell_tools(registry)
    tool = registry.get("powershell_tool")
    assert tool is not None
    with pytest.raises(ValueError, match="pwsh"):
        await tool.handler({"command": "Get-Date"})
