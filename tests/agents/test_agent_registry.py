"""Markdown-defined agent types + registry + spec bridge (coordination C2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.agents import (
    AgentDefinition,
    AgentRegistry,
    agent_definition_to_spec,
    load_agent_definitions,
    parse_agent_markdown,
)

_EXPLORER_MD = """---
name: explorer
description: Read-only investigator.
when_to_use: Use before making changes.
tools: [read, grep, web_search]
denied_tools: [bash]
model_role: simple
reasoning_effort: low
max_tool_calls: 20
deadline_seconds: 45
max_cost_usd: 0.25
custom_key: hello
---
You are a read-only explorer. Investigate and report concise findings.
"""


def test_parse_agent_markdown_maps_all_fields() -> None:
    d = parse_agent_markdown(_EXPLORER_MD, source="filesystem")
    assert d.name == "explorer"
    assert d.description == "Read-only investigator."
    assert d.when_to_use == "Use before making changes."
    assert d.allowed_tools == ("read", "grep", "web_search")
    assert d.denied_tools == ("bash",)
    assert d.model_role == "simple"
    assert d.reasoning_effort == "low"
    assert d.max_tool_calls == 20
    assert d.deadline_seconds == 45.0
    assert d.max_cost_usd == 0.25
    assert d.source == "filesystem"
    assert d.metadata == {"custom_key": "hello"}  # unknown keys preserved
    assert d.system_prompt.startswith("You are a read-only explorer")


def test_parse_accepts_csv_tools_and_name_fallback() -> None:
    d = parse_agent_markdown(
        "---\ntools: read, grep\n---\nBody.", name_fallback="worker"
    )
    assert d.name == "worker"  # no frontmatter name → fallback
    assert d.allowed_tools == ("read", "grep")


def test_parse_without_name_or_fallback_raises() -> None:
    with pytest.raises(ValueError):
        parse_agent_markdown("---\ndescription: x\n---\nBody.")


def test_body_is_the_system_prompt() -> None:
    d = parse_agent_markdown("---\nname: a\n---\n# Title\nDo the thing.")
    assert "Do the thing." in d.system_prompt
    assert d.description == "Title"  # first heading used when no description


def test_registry_priority_overrides_and_first_wins_within_priority() -> None:
    reg = AgentRegistry()
    builtin = AgentDefinition(name="explorer", description="builtin")
    project = AgentDefinition(name="explorer", description="project")
    assert reg.register(builtin, priority=0) is True
    assert reg.register(project, priority=10) is True  # higher wins
    assert reg.get("explorer").description == "project"
    assert reg.register(builtin, priority=0) is False  # lower can't reclaim
    other = AgentDefinition(name="explorer", description="other")
    assert reg.register(other, priority=10) is False  # equal priority → first wins
    assert reg.get("explorer").description == "project"


def test_registry_membership_and_listing() -> None:
    reg = AgentRegistry()
    reg.register_all(
        [AgentDefinition(name="b"), AgentDefinition(name="a")], priority=1
    )
    assert reg.names() == ["a", "b"]  # sorted
    assert "a" in reg and "z" not in reg
    assert len(reg) == 2
    assert reg.get("missing") is None


def test_load_agent_definitions_from_directory(tmp_path: Path) -> None:
    (tmp_path / "explorer.md").write_text(_EXPLORER_MD, encoding="utf-8")
    (tmp_path / "nameless.md").write_text("---\ntools: read\n---\nBody.", "utf-8")
    (tmp_path / "broken.md").write_text("---\n---\n", "utf-8")  # no name/fallback? has stem
    defs = load_agent_definitions(tmp_path)
    names = {d.name for d in defs}
    assert "explorer" in names
    assert "nameless" in names  # file stem as fallback


def test_load_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert load_agent_definitions(tmp_path / "nope") == []


def test_agent_definition_to_spec_bridges_to_subagent_spec() -> None:
    d = parse_agent_markdown(_EXPLORER_MD)
    spec = agent_definition_to_spec(d, prompt="Find the West total")
    assert spec.agent_type == "explorer"
    assert spec.prompt == "Find the West total"
    assert spec.system_prompt.startswith("You are a read-only explorer")
    assert spec.allowed_tools == ("read", "grep", "web_search")
    assert spec.denied_tools == ("bash",)
    assert spec.model_role == "simple"
    assert spec.reasoning_effort == "low"
    assert spec.max_tool_calls == 20
    assert spec.max_cost_usd == 0.25


def test_spec_bridge_honors_agent_type_override() -> None:
    d = AgentDefinition(name="explorer")
    spec = agent_definition_to_spec(d, prompt="go", agent_type="explorer#3")
    assert spec.agent_type == "explorer#3"
