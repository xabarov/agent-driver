"""Tests for default built-in filesystem/codebase tools."""

from __future__ import annotations

import json

import pytest

from agent_driver.tools import register_builtin_tools, register_planning_tool
from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.registry import ToolRegistry
from tests.tools._builtin_expected import EXPECTED_BUILTIN_TOOL_NAMES


@pytest.mark.asyncio
async def test_register_builtin_tools_contains_first_wave_names() -> None:
    """Default built-ins should include filesystem and web first-wave tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    for name in EXPECTED_BUILTIN_TOOL_NAMES:
        assert registry.get(name) is not None


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


@pytest.mark.asyncio
async def test_read_file_rejects_relative_path(tmp_path) -> None:
    """read_file should enforce absolute path contract."""
    _ = tmp_path
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with pytest.raises(ValueError, match="absolute"):
        await tool.handler({"path": "relative.txt"})


@pytest.mark.asyncio
async def test_glob_respects_gitignore(tmp_path) -> None:
    """glob_search should skip paths matched by .gitignore patterns."""
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "seen.py").write_text("x = 2\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path), "pattern": "*.py"})
    assert "ignored.py" not in out["results"]
    assert "seen.py" in out["results"]


@pytest.mark.asyncio
async def test_grep_honors_path_glob_filter(tmp_path) -> None:
    """grep_search should filter candidate files with path_glob."""
    pkg = tmp_path / "pkg"
    docs = tmp_path / "docs"
    pkg.mkdir()
    docs.mkdir()
    (pkg / "a.py").write_text("needle\n", encoding="utf-8")
    (docs / "a.md").write_text("needle\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "needle", "path_glob": "*.py"}
    )
    assert out["matches"]
    assert all(row["path"].endswith(".py") for row in out["matches"])


@pytest.mark.asyncio
async def test_read_file_respects_offset_and_limit(tmp_path) -> None:
    """read_file should slice content deterministically by offset/limit."""
    target = tmp_path / "note.txt"
    target.write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    out = await tool.handler({"path": str(target), "offset": 2, "limit": 2})
    assert out["returned_lines"] == 2
    assert out["content"].splitlines() == ["2|l2", "3|l3"]


@pytest.mark.asyncio
async def test_read_file_rejects_when_file_exceeds_max_bytes(tmp_path) -> None:
    """read_file should guard against oversized files."""
    target = tmp_path / "large.txt"
    target.write_text("x" * 64, encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with pytest.raises(ValueError, match="max_bytes"):
        await tool.handler({"path": str(target), "max_bytes": 8})


@pytest.mark.asyncio
async def test_glob_respects_max_depth(tmp_path) -> None:
    """glob_search should skip deep paths beyond max_depth."""
    shallow = tmp_path / "a.py"
    deep_dir = tmp_path / "pkg" / "sub"
    deep_dir.mkdir(parents=True)
    deep = deep_dir / "b.py"
    shallow.write_text("x=1\n", encoding="utf-8")
    deep.write_text("x=2\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "*.py", "max_depth": 0}
    )
    assert out["results"] == ["a.py"]


@pytest.mark.asyncio
async def test_grep_respects_max_matches_limit(tmp_path) -> None:
    """grep_search should cap returned matches by max_matches."""
    target = tmp_path / "log.txt"
    target.write_text("hit\nhit\nhit\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "hit", "max_matches": 2}
    )
    assert len(out["matches"]) == 2


@pytest.mark.asyncio
async def test_grep_rejects_invalid_regex(tmp_path) -> None:
    """grep_search should fail fast on invalid regex syntax."""
    _ = tmp_path
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    with pytest.raises(Exception):
        await tool.handler({"base_dir": str(tmp_path), "pattern": "("})


@pytest.mark.asyncio
async def test_file_write_overwrite_and_append(tmp_path) -> None:
    """file_write should support deterministic overwrite/append semantics."""
    target = tmp_path / "note.txt"
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_write")
    assert tool is not None
    first = await tool.handler({"path": str(target), "content": "alpha\n"})
    assert first["mode"] == "overwrite"
    assert target.read_text(encoding="utf-8") == "alpha\n"
    second = await tool.handler(
        {"path": str(target), "content": "beta\n", "mode": "append"}
    )
    assert second["mode"] == "append"
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


@pytest.mark.asyncio
async def test_file_write_can_create_parent_when_flag_enabled(tmp_path) -> None:
    """file_write should create missing parent only when explicitly allowed."""
    nested = tmp_path / "a" / "b" / "note.txt"
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_write")
    assert tool is not None
    with pytest.raises(ValueError, match="create_parent=true"):
        await tool.handler({"path": str(nested), "content": "x"})
    await tool.handler({"path": str(nested), "content": "x", "create_parent": True})
    assert nested.read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_file_write_dry_run_does_not_change_file(tmp_path) -> None:
    """file_write dry_run should return preview without persisting changes."""
    target = tmp_path / "note.txt"
    target.write_text("alpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_write")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "content": "beta\n",
            "mode": "append",
            "dry_run": True,
        }
    )
    assert out["dry_run"] is True
    assert out["operation"] == "write"
    assert out["preview"]["after"].endswith("beta\n")
    assert target.read_text(encoding="utf-8") == "alpha\n"


@pytest.mark.asyncio
async def test_file_write_append_respects_max_bytes(tmp_path) -> None:
    """file_write append should fail when resulting size exceeds max_bytes."""
    target = tmp_path / "note.txt"
    target.write_text("abcd", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_write")
    assert tool is not None
    with pytest.raises(ValueError, match="max_bytes"):
        await tool.handler(
            {
                "path": str(target),
                "content": "ef",
                "mode": "append",
                "max_bytes": 5,
            }
        )


@pytest.mark.asyncio
async def test_file_edit_replaces_expected_occurrences(tmp_path) -> None:
    """file_edit should replace exactly expected old_text occurrences."""
    target = tmp_path / "cfg.txt"
    target.write_text("name=old\nname=old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_edit")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "old_text": "name=old",
            "new_text": "name=new",
            "expected_occurrences": 2,
        }
    )
    assert out["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "name=new\nname=new\n"


@pytest.mark.asyncio
async def test_file_edit_dry_run_preserves_existing_newlines(tmp_path) -> None:
    """file_edit dry_run should preserve file content and expose preview."""
    target = tmp_path / "cfg.txt"
    target.write_text("name=old\r\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_edit")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "old_text": "old",
            "new_text": "new",
            "dry_run": True,
        }
    )
    assert out["dry_run"] is True
    assert out["operation"] == "edit"
    assert target.read_bytes() == b"name=old\r\n"
    assert out["preview"]["before"] == "name=old\r\n"
    assert out["preview"]["after"] == "name=new\r\n"


@pytest.mark.asyncio
async def test_file_edit_fails_on_occurrence_mismatch(tmp_path) -> None:
    """file_edit should fail fast when expected occurrence count mismatches."""
    target = tmp_path / "cfg.txt"
    target.write_text("name=old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_edit")
    assert tool is not None
    with pytest.raises(ValueError, match="occurrences mismatch"):
        await tool.handler(
            {
                "path": str(target),
                "old_text": "name=old",
                "new_text": "name=new",
                "expected_occurrences": 2,
            }
        )


def _write_notebook(path, *, code: str = "print('a')\n") -> None:
    payload = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [code],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_notebook_edit_replaces_existing_cell_text(tmp_path) -> None:
    """notebook_edit should replace old_text exactly once in target cell."""
    target = tmp_path / "nb.ipynb"
    _write_notebook(target, code="print('old')\n")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("notebook_edit")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "cell_idx": 0,
            "is_new_cell": False,
            "old_text": "old",
            "new_text": "new",
        }
    )
    assert out["operation"] == "replace"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n"]


@pytest.mark.asyncio
async def test_notebook_edit_inserts_new_cell(tmp_path) -> None:
    """notebook_edit should insert a new cell at requested index."""
    target = tmp_path / "nb.ipynb"
    _write_notebook(target, code="print('a')\n")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("notebook_edit")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "cell_idx": 1,
            "is_new_cell": True,
            "cell_type": "markdown",
            "old_text": "",
            "new_text": "# title\n",
        }
    )
    assert out["operation"] == "insert"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert len(rendered["cells"]) == 2
    assert rendered["cells"][1]["cell_type"] == "markdown"
    assert rendered["cells"][1]["source"] == ["# title\n"]


@pytest.mark.asyncio
async def test_notebook_edit_fails_when_old_text_mismatch(tmp_path) -> None:
    """notebook_edit should fail when old_text is absent or ambiguous."""
    target = tmp_path / "nb.ipynb"
    _write_notebook(target, code="print('same')\nprint('same')\n")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("notebook_edit")
    assert tool is not None
    with pytest.raises(ValueError, match="exactly once"):
        await tool.handler(
            {
                "path": str(target),
                "cell_idx": 0,
                "is_new_cell": False,
                "old_text": "same",
                "new_text": "new",
            }
        )


@pytest.mark.asyncio
async def test_notebook_edit_preserves_list_source_roundtrip(tmp_path) -> None:
    """notebook_edit should keep list source shape for list-backed cells."""
    target = tmp_path / "nb.ipynb"
    _write_notebook(target, code="print('old')\nprint('keep')\n")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("notebook_edit")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "cell_idx": 0,
            "is_new_cell": False,
            "old_text": "old",
            "new_text": "new",
        }
    )
    assert out["replacements"] == 1
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n", "print('keep')\n"]
