"""Tests for default built-in filesystem/codebase tools."""

from __future__ import annotations

import pytest

from agent_driver.tools.registry import ToolRegistry
from agent_driver.tools import register_builtin_tools
from agent_driver.tools.builtin.filesystem import register_filesystem_tools


@pytest.mark.asyncio
async def test_register_builtin_tools_contains_first_wave_names() -> None:
    """Default built-ins should include filesystem and web first-wave tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    assert registry.get("read_file") is not None
    assert registry.get("glob_search") is not None
    assert registry.get("grep_search") is not None
    assert registry.get("web_fetch") is not None
    assert registry.get("web_search") is not None


@pytest.mark.asyncio
async def test_read_file_tool_returns_numbered_lines(tmp_path) -> None:
    """read_file should return numbered content lines."""
    target = tmp_path / "note.txt"
    target.write_text("line_a\nline_b\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    result = await tool.handler({"path": str(target)})
    assert result["returned_lines"] == 2
    assert "1|line_a" in result["content"]
    assert "2|line_b" in result["content"]


@pytest.mark.asyncio
async def test_glob_and_grep_tools_find_expected_files(tmp_path) -> None:
    """glob_search and grep_search should return deterministic matches."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    app_file = src_dir / "app.py"
    app_file.write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("main appears here\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    glob_tool = registry.get("glob_search")
    grep_tool = registry.get("grep_search")
    assert glob_tool is not None
    assert grep_tool is not None
    glob_out = await glob_tool.handler({"base_dir": str(tmp_path), "pattern": "*.py"})
    assert glob_out["results"] == ["src/app.py"]
    grep_out = await grep_tool.handler({"base_dir": str(tmp_path), "pattern": "main"})
    paths = {row["path"] for row in grep_out["matches"]}
    assert "src/app.py" in paths
    assert "README.md" in paths
