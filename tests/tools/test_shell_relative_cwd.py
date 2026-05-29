"""Tests for workspace-scoped default cwd in bash tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.shell import register_shell_tools
from agent_driver.tools.context import workspace_cwd_scope
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_bash_uses_workspace_scope_when_cwd_omitted(tmp_path) -> None:
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with workspace_cwd_scope(tmp_path):
        out = await tool.handler({"command": "pwd"})
    assert out["exit_code"] == 0
    assert out["cwd"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_bash_rejects_cwd_outside_workspace_scope(tmp_path) -> None:
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    jail = tmp_path / "jail"
    outside = tmp_path / "outside"
    jail.mkdir()
    outside.mkdir()
    with workspace_cwd_scope(jail):
        with pytest.raises(ValueError, match="cwd outside workspace"):
            await tool.handler({"command": "pwd", "cwd": str(outside)})
