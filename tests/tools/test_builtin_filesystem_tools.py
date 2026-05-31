"""Tests for default built-in filesystem/codebase tools."""

from __future__ import annotations

import json

import pytest

from agent_driver.tools import register_builtin_tools, register_planning_tool
from agent_driver.tools.builtin.filesystem import register_filesystem_tools
from agent_driver.tools.context import workspace_cwd_scope
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
    glob_out = await glob_tool.handler(
        {"base_dir": str(tmp_path), "pattern": "*.py", "recursive": True}
    )
    assert glob_out["results"] == ["src/app.py"]
    grep_out = await grep_tool.handler({"base_dir": str(tmp_path), "pattern": "main"})
    paths = {row["path"] for row in grep_out["matches"]}
    assert "src/app.py" in paths
    assert "README.md" in paths


@pytest.mark.asyncio
async def test_read_file_resolves_relative_path_with_workspace_scope(tmp_path) -> None:
    """read_file should resolve relative path when workspace scope is set."""
    target = tmp_path / "relative.txt"
    target.write_text("ok\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with workspace_cwd_scope(tmp_path):
        out = await tool.handler({"path": "relative.txt"})
    assert out["returned_lines"] == 1


@pytest.mark.asyncio
async def test_artifact_tools_list_read_and_preview_workspace_artifacts(
    tmp_path,
) -> None:
    """Artifact tools should expose bounded research artifact reads."""
    report = tmp_path / "research" / "report.md"
    report.parent.mkdir()
    report.write_text("# Report\n\nbody\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    list_tool = registry.get("artifact_list")
    read_tool = registry.get("artifact_read")
    preview_tool = registry.get("artifact_preview")
    assert list_tool is not None
    assert read_tool is not None
    assert preview_tool is not None

    with workspace_cwd_scope(tmp_path):
        listed = await list_tool.handler({})
        read = await read_tool.handler({"path": "research/report.md"})
        preview = await preview_tool.handler({"path": "research/report.md"})

    assert listed["artifacts"][0]["path"] == "research/report.md"
    assert listed["artifacts"][0]["kind"] == "report"
    assert read["content"] == "# Report\n\nbody\n"
    assert read["truncated"] is False
    assert preview["headings"] == ["# Report"]
    assert preview["preview"] == "# Report\n\nbody\n"


@pytest.mark.asyncio
async def test_artifact_tools_reject_non_artifact_paths(tmp_path) -> None:
    """Artifact tools should not become a second unrestricted read_file."""
    (tmp_path / "note.txt").write_text("secret", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    read_tool = registry.get("artifact_read")
    assert read_tool is not None

    with workspace_cwd_scope(tmp_path):
        with pytest.raises(ValueError, match="known artifact"):
            await read_tool.handler({"path": "note.txt"})


@pytest.mark.asyncio
async def test_artifact_read_truncates_large_artifact(tmp_path) -> None:
    """artifact_read should return a bounded preview instead of failing."""
    report = tmp_path / "research" / "report.md"
    report.parent.mkdir()
    report.write_text("abcdef", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    read_tool = registry.get("artifact_read")
    assert read_tool is not None

    with workspace_cwd_scope(tmp_path):
        out = await read_tool.handler({"path": "research/report.md", "max_bytes": 3})

    assert out["content"] == "abc"
    assert out["truncated"] is True
    assert out["size_bytes"] == 6


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
async def test_glob_respects_gitignore_directory_pattern(tmp_path) -> None:
    """Directory ignore entries should hide nested files under that prefix."""
    (tmp_path / ".gitignore").write_text(".agent-driver/\n", encoding="utf-8")
    hidden = tmp_path / ".agent-driver" / "evals"
    hidden.mkdir(parents=True)
    (hidden / "report.md").write_text("hidden\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("visible\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "*.md", "recursive": True}
    )
    assert "README.md" in out["results"]
    assert not any(path.startswith(".agent-driver/") for path in out["results"])


@pytest.mark.asyncio
async def test_glob_pattern_star_is_non_recursive_by_default(tmp_path) -> None:
    """Simple star pattern should match only top-level entries."""
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "top.txt").write_text("x\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path), "pattern": "*.txt"})
    assert out["results"] == ["top.txt"]


@pytest.mark.asyncio
async def test_glob_rejects_parent_path_pattern(tmp_path) -> None:
    """Parent directory traversal in pattern should be rejected."""
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    with pytest.raises(ValueError, match="workspace-relative"):
        await tool.handler({"base_dir": str(tmp_path), "pattern": "../*"})


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
async def test_read_file_rejects_offset_zero(tmp_path) -> None:
    """read_file should reject offset=0 to avoid silent empty slices."""
    target = tmp_path / "note.txt"
    target.write_text("l1\nl2\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with pytest.raises(ValueError, match="offset"):
        await tool.handler({"path": str(target), "offset": 0})


@pytest.mark.asyncio
async def test_read_file_rejects_limit_zero(tmp_path) -> None:
    """read_file should reject limit=0 to avoid silent empty slices."""
    target = tmp_path / "note.txt"
    target.write_text("l1\nl2\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with pytest.raises(ValueError, match="limit"):
        await tool.handler({"path": str(target), "limit": 0})


@pytest.mark.asyncio
async def test_read_file_rejects_offset_beyond_eof(tmp_path) -> None:
    """read_file should fail loudly when offset is beyond available lines."""
    target = tmp_path / "note.txt"
    target.write_text("l1\nl2\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("read_file")
    assert tool is not None
    with pytest.raises(ValueError, match="line count"):
        await tool.handler({"path": str(target), "offset": 10})


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
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_glob_directory_pattern_returns_directories_only(tmp_path) -> None:
    """glob_search with trailing slash should return directories, not files."""
    src = tmp_path / "src"
    nested = src / "nested"
    src.mkdir()
    nested.mkdir()
    (src / "app.py").write_text("x=1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path), "pattern": "**/"})
    assert "src/" in out["results"]
    assert "src/nested/" in out["results"]
    assert "src/app.py" not in out["results"]


@pytest.mark.asyncio
async def test_glob_sets_truncated_metadata_on_cap(tmp_path) -> None:
    """glob_search should expose cap metadata when max_results is reached."""
    for idx in range(3):
        (tmp_path / f"f{idx}.py").write_text("x=1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("glob_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "*.py", "max_results": 2}
    )
    assert out["returned_count"] == 2
    assert out["truncated"] is True
    assert out["limit"] == "max_results"
    assert out["limit_value"] == 2
    assert out["more_available"] is True


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
    assert out["truncated"] is True
    assert out["limit"] == "max_matches"
    assert out["limit_value"] == 2


@pytest.mark.asyncio
async def test_grep_auto_prefixes_simple_path_glob(tmp_path) -> None:
    """grep_search should treat '*.py' path_glob as recursive by default."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("needle\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "needle", "path_glob": "*.py"}
    )
    assert out["matches"]
    assert out["matches"][0]["path"] == "src/a.py"


@pytest.mark.asyncio
async def test_grep_reports_skipped_files_count(tmp_path) -> None:
    """grep_search should expose number of unreadable/skipped files."""
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x00")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path), "pattern": "needle"})
    assert out["skipped_files_count"] >= 1


@pytest.mark.asyncio
async def test_grep_marks_more_lines_in_file_when_capped(tmp_path) -> None:
    """grep_search should flag that one file has additional matching lines."""
    target = tmp_path / "log.txt"
    target.write_text("hit\nhit\nhit\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("grep_search")
    assert tool is not None
    out = await tool.handler(
        {"base_dir": str(tmp_path), "pattern": "hit", "max_matches": 1}
    )
    assert out["matches"][0]["more_lines_in_file"] is True


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


@pytest.mark.asyncio
async def test_file_patch_applies_multiple_replacements(tmp_path) -> None:
    """file_patch should apply ordered exact replacements in one call."""
    target = tmp_path / "report.md"
    target.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_patch")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "patches": [
                {
                    "old_text": "alpha",
                    "new_text": "ALPHA",
                    "expected_occurrences": 2,
                },
                {"old_text": "beta", "new_text": "BETA"},
            ],
        }
    )
    assert out["operation"] == "patch"
    assert out["replacements"] == 3
    assert [item["replacements"] for item in out["patches_applied"]] == [2, 1]
    assert target.read_text(encoding="utf-8") == "ALPHA\nBETA\nALPHA\n"


@pytest.mark.asyncio
async def test_file_patch_dry_run_preserves_file(tmp_path) -> None:
    """file_patch dry_run should expose preview without writing the patch."""
    target = tmp_path / "report.md"
    target.write_text("one\ntwo\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_patch")
    assert tool is not None
    out = await tool.handler(
        {
            "path": str(target),
            "patches": [
                {"old_text": "one", "new_text": "ONE"},
                {"old_text": "two", "new_text": "TWO"},
            ],
            "dry_run": True,
        }
    )
    assert out["dry_run"] is True
    assert out["preview"]["after"] == "ONE\nTWO\n"
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"


@pytest.mark.asyncio
async def test_file_patch_fails_on_patch_occurrence_mismatch(tmp_path) -> None:
    """file_patch should stop before writing when any patch count mismatches."""
    target = tmp_path / "report.md"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_patch")
    assert tool is not None
    with pytest.raises(ValueError, match="index=1"):
        await tool.handler(
            {
                "path": str(target),
                "patches": [
                    {"old_text": "alpha", "new_text": "ALPHA"},
                    {
                        "old_text": "beta",
                        "new_text": "BETA",
                        "expected_occurrences": 2,
                    },
                ],
            }
        )
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


@pytest.mark.asyncio
async def test_file_patch_rejects_too_many_patch_items(tmp_path) -> None:
    """file_patch should enforce its schema-sized batch limit at runtime."""
    target = tmp_path / "report.md"
    target.write_text("alpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    tool = registry.get("file_patch")
    assert tool is not None
    with pytest.raises(ValueError, match="at most 50"):
        await tool.handler(
            {
                "path": str(target),
                "patches": [
                    {"old_text": "alpha", "new_text": "alpha"} for _ in range(51)
                ],
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
