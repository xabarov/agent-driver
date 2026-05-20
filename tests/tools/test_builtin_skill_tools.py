"""Tests for built-in skill discovery tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.skills import register_skill_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_skill_tool_discovers_skill_files_with_provenance(tmp_path) -> None:
    """skill_tool should discover SKILL.md and expose provenance metadata."""
    skill_file = tmp_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Alpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path)})
    assert out["base_dir"] == str(tmp_path)
    skills = out["skills"]
    assert len(skills) == 1
    row = skills[0]
    assert row["name"] == "alpha"
    assert row["relative_path"] == "skills/alpha/SKILL.md"
    assert row["provenance"]["source"] == "filesystem"
    assert row["trusted"] is False


@pytest.mark.asyncio
async def test_skill_tool_marks_trusted_roots(tmp_path) -> None:
    """skill_tool should mark entries trusted when under trusted_roots."""
    trusted_root = tmp_path / "trusted"
    trusted_file = trusted_root / "team" / "SKILL.md"
    trusted_file.parent.mkdir(parents=True)
    trusted_file.write_text("# Team\n", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "base_dir": str(tmp_path),
            "trusted_roots": [str(trusted_root)],
        }
    )
    assert out["skills"]
    assert out["skills"][0]["trusted"] is True


@pytest.mark.asyncio
async def test_skill_tool_reports_truncated_metadata(tmp_path) -> None:
    """skill_tool should expose cap metadata when max_results is reached."""
    for idx in range(3):
        skill_file = tmp_path / f"team{idx}" / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text("# Skill\n", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path), "max_results": 2})
    assert out["returned_count"] == 2
    assert out["truncated"] is True
    assert out["more_available"] is True
