"""Tests for workspace-scoped relative filesystem paths."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.context import workspace_cwd_scope
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_read_file_resolves_relative_path_from_workspace_scope(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("a\nb\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with workspace_cwd_scope(tmp_path):
        out = await tool.handler({"path": "note.txt"})
    assert out["returned_lines"] == 2
    assert "1|a" in out["content"]


@pytest.mark.asyncio
async def test_file_write_resolves_relative_path_from_workspace_scope(tmp_path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_write")
    assert tool is not None
    with workspace_cwd_scope(tmp_path):
        out = await tool.handler({"path": "w.txt", "content": "ok"})
    assert out["path"] == str((tmp_path / "w.txt").resolve())
    assert (tmp_path / "w.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_search_tools_resolve_relative_base_dir_from_workspace_scope(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("needle\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    glob_tool = registry.get("glob_search")
    grep_tool = registry.get("grep_search")
    assert glob_tool is not None
    assert grep_tool is not None
    with workspace_cwd_scope(tmp_path):
        glob_out = await glob_tool.handler({"base_dir": "src", "pattern": "*.py"})
        grep_out = await grep_tool.handler({"base_dir": "src", "pattern": "needle"})
    assert glob_out["results"] == ["x.py"]
    assert grep_out["matches"]
    assert grep_out["matches"][0]["path"] == "x.py"
