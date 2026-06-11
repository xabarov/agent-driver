"""Tests for shared skills package."""

from __future__ import annotations

from agent_driver.skills import load_skill_manifest, parse_frontmatter


def test_parse_frontmatter_supports_skill_metadata() -> None:
    """Frontmatter parser should cover the metadata fields used by skills."""
    payload = parse_frontmatter(
        """
name: research-helper
description: Helps with research
when_to_use: source-heavy tasks
version: 1.2.3
tags: [research, citations]
allowed_tools:
  - web_search
  - web_fetch
context:
  depth: source_verified_report
agent:
  profile: react
""".strip()
    )

    assert payload["name"] == "research-helper"
    assert payload["tags"] == ["research", "citations"]
    assert payload["allowed_tools"] == ["web_search", "web_fetch"]
    assert payload["context"]["depth"] == "source_verified_report"
    assert payload["agent"]["profile"] == "react"


def test_load_skill_manifest_indexes_supporting_files_and_warnings(tmp_path) -> None:
    """Manifest loader should return metadata, support index and safety warnings."""
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: alpha
description: Alpha skill
allowed_tools: [python]
---
# Alpha Body
""",
        encoding="utf-8",
    )
    (skill_dir / "notes.md").write_text("notes", encoding="utf-8")

    manifest = load_skill_manifest(skill_file, base_dir=tmp_path)

    assert manifest.name == "alpha"
    assert manifest.description == "Alpha skill"
    assert manifest.relative_path == "skills/alpha/SKILL.md"
    assert manifest.supporting_files[0]["path"] == "notes.md"
    assert manifest.trusted is False
    assert manifest.safety_warnings
