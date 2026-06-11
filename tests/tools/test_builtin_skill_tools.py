"""Tests for built-in skill discovery tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.context import workspace_cwd_scope
from agent_driver.tools.builtin.skills import register_skill_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_skill_tool_discovers_skill_files_with_provenance(tmp_path) -> None:
    """skill_tool should discover SKILL.md and expose provenance metadata."""
    skill_file = tmp_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """---
name: alpha-skill
description: Alpha description
when_to_use: alpha tasks
version: 1.0.0
tags: [alpha, demo]
allowed_tools: [web_search]
---
# Alpha
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None
    out = await tool.handler({"base_dir": str(tmp_path)})
    assert out["base_dir"] == str(tmp_path)
    skills = out["skills"]
    assert len(skills) == 1
    row = skills[0]
    assert row["name"] == "alpha-skill"
    assert row["description"] == "Alpha description"
    assert row["when_to_use"] == "alpha tasks"
    assert row["version"] == "1.0.0"
    assert row["tags"] == ["alpha", "demo"]
    assert row["allowed_tools"] == ["web_search"]
    assert row["relative_path"] == "skills/alpha/SKILL.md"
    assert row["skill_dir"] == str(skill_file.parent)
    assert row["source"] == "filesystem"
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
async def test_skill_tool_allows_trusted_root_outside_workspace_jail(tmp_path) -> None:
    """Bundled trusted skills should remain readable from a session workspace."""
    workspace = tmp_path / "workspace" / "session_1"
    trusted_root = tmp_path / "agent_driver" / "skills" / "curated"
    skill_file = trusted_root / "deep-research-report" / "SKILL.md"
    workspace.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """---
name: deep-research-report
description: Research report workflow
---
# Deep Research
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None

    with workspace_cwd_scope(workspace):
        out = await tool.handler(
            {
                "base_dir": str(trusted_root),
                "trusted_roots": [str(trusted_root)],
            }
        )

    assert out["base_dir"] == str(trusted_root)
    assert out["skills"][0]["name"] == "deep-research-report"
    assert out["skills"][0]["trusted"] is True


@pytest.mark.asyncio
async def test_skill_tool_rejects_untrusted_root_outside_workspace_jail(
    tmp_path,
) -> None:
    """The trusted-root bypass must not reopen arbitrary host paths."""
    workspace = tmp_path / "workspace" / "session_1"
    outside_root = tmp_path / "outside" / "skills"
    skill_file = outside_root / "unknown" / "SKILL.md"
    workspace.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Unknown\n", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_tool")
    assert tool is not None

    with (
        workspace_cwd_scope(workspace),
        pytest.raises(ValueError, match="path outside workspace"),
    ):
        await tool.handler({"base_dir": str(outside_root)})


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


@pytest.mark.asyncio
async def test_skill_view_loads_body_with_invocation_record(tmp_path) -> None:
    """skill_view should load SKILL.md content only when requested."""
    skill_file = tmp_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """---
name: alpha
description: Alpha description
---
# Alpha Body
Use carefully.
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_view")
    assert tool is not None

    out = await tool.handler(
        {
            "base_dir": str(tmp_path),
            "name": "alpha",
            "trusted_roots": [str(tmp_path / "skills")],
            "agent_id": "agent",
        }
    )

    assert out["skill"]["name"] == "alpha"
    assert out["trusted"] is True
    assert out["content_kind"] == "skill"
    assert "# Alpha Body" in out["content"]
    assert out["skill_invocation"]["name"] == "alpha"
    assert out["skill_invocation"]["trusted"] is True
    assert out["skill_invocation"]["agent_id"] == "agent"


@pytest.mark.asyncio
async def test_skill_view_allows_trusted_root_outside_workspace_jail(tmp_path) -> None:
    """skill_view should load trusted bundled skills from outside session cwd."""
    workspace = tmp_path / "workspace" / "session_1"
    trusted_root = tmp_path / "agent_driver" / "skills" / "curated"
    skill_file = trusted_root / "deep-research-report" / "SKILL.md"
    workspace.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """---
name: deep-research-report
description: Research report workflow
---
# Deep Research
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_view")
    assert tool is not None

    with workspace_cwd_scope(workspace):
        out = await tool.handler(
            {
                "base_dir": str(trusted_root),
                "name": "deep-research-report",
                "trusted_roots": [str(trusted_root)],
                "agent_id": "agent",
            }
        )

    assert out["skill"]["name"] == "deep-research-report"
    assert out["trusted"] is True
    assert "# Deep Research" in out["content"]


@pytest.mark.asyncio
async def test_skill_view_rejects_untrusted_root_outside_workspace_jail(
    tmp_path,
) -> None:
    """skill_view still rejects untrusted absolute paths outside the session."""
    workspace = tmp_path / "workspace" / "session_1"
    outside_root = tmp_path / "outside" / "skills"
    skill_file = outside_root / "unknown" / "SKILL.md"
    workspace.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Unknown\n", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_view")
    assert tool is not None

    with (
        workspace_cwd_scope(workspace),
        pytest.raises(ValueError, match="path outside workspace"),
    ):
        await tool.handler({"base_dir": str(outside_root), "name": "unknown"})


@pytest.mark.asyncio
async def test_skill_view_loads_supporting_file_inside_skill_dir(tmp_path) -> None:
    """skill_view should load one supporting file and reject path escape."""
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Alpha\n", encoding="utf-8")
    (skill_dir / "guide.md").write_text("guide", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry)
    tool = registry.get("skill_view")
    assert tool is not None

    out = await tool.handler(
        {
            "base_dir": str(tmp_path),
            "skill_dir": str(skill_dir),
            "relative_file": "guide.md",
        }
    )

    assert out["content_kind"] == "supporting_file"
    assert out["relative_file"] == "guide.md"
    assert out["content"] == "guide"
    with pytest.raises(ValueError):
        await tool.handler(
            {
                "base_dir": str(tmp_path),
                "skill_dir": str(skill_dir),
                "relative_file": "../secret.txt",
            }
        )
