"""Tests for shared skills package."""

from __future__ import annotations

import sys

from agent_driver.skills import load_skill_manifest, parse_frontmatter
from agent_driver.skills.parser import _parse_frontmatter_legacy


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


# --- S4: real YAML parsing (deep nesting / block scalars / string-first) --------


def test_parse_frontmatter_handles_deep_nesting() -> None:
    """The hand-rolled parser only reached one level; real YAML goes arbitrarily deep."""
    payload = parse_frontmatter(
        "context:\n  outer:\n    inner:\n      leaf: value\n"
    )
    assert payload["context"]["outer"]["inner"]["leaf"] == "value"


def test_parse_frontmatter_handles_block_scalar() -> None:
    payload = parse_frontmatter("description: |\n  line one\n  line two\n")
    assert payload["description"] == "line one\nline two\n"


def test_parse_frontmatter_is_string_first_no_implicit_typing() -> None:
    """String-first (BaseLoader): version stays a string and no/yes stay strings
    (avoids the YAML implicit-typing / Norway-problem footguns)."""
    payload = parse_frontmatter("version: 1.0\ntags: [research, no, yes]\n")
    assert payload["version"] == "1.0"
    assert payload["tags"] == ["research", "no", "yes"]


def test_parse_frontmatter_non_mapping_returns_empty() -> None:
    assert parse_frontmatter("just a bare string") == {}
    assert parse_frontmatter("") == {}


def test_parse_frontmatter_falls_back_without_pyyaml(monkeypatch) -> None:
    """Without PyYAML (only a transitive dep) the conservative parser still works."""
    monkeypatch.setitem(sys.modules, "yaml", None)  # force ImportError on `import yaml`
    payload = parse_frontmatter("name: x\ntags: [a, b]\ncontext:\n  depth: hard\n")
    assert payload["name"] == "x"
    assert payload["tags"] == ["a", "b"]
    assert payload["context"]["depth"] == "hard"


def test_parse_frontmatter_invalid_yaml_falls_back() -> None:
    """Malformed YAML degrades to the hand-rolled parser rather than raising."""
    # Unclosed flow sequence — YAMLError under BaseLoader.
    payload = parse_frontmatter("name: ok\nbad: [unclosed\n")
    assert payload["name"] == "ok"


def test_legacy_parser_still_covers_simple_shapes() -> None:
    payload = _parse_frontmatter_legacy(
        "name: x\ntags: [a, b]\nallowed_tools:\n  - t1\ncontext:\n  depth: hard\n"
    )
    assert payload["name"] == "x"
    assert payload["tags"] == ["a", "b"]
    assert payload["allowed_tools"] == ["t1"]
    assert payload["context"]["depth"] == "hard"


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
