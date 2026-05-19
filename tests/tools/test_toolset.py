"""Tests for ToolSet filtering and prompt-surface selection."""

from __future__ import annotations

from agent_driver.contracts import AgentProfile, ToolRisk
from agent_driver.tools import (
    ToolRegistry,
    ToolSet,
    register_builtin_tools,
    register_planning_tool,
    render_tool_docs,
)


def _registry_with_defaults() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    return registry


def test_toolset_only_filters_registry_and_prompt_docs() -> None:
    """Explicit ToolSet.only should restrict execution and rendered docs."""
    registry = _registry_with_defaults()
    toolset = ToolSet.only("web_fetch")
    filtered = toolset.apply(registry)
    assert filtered.list_names() == ["web_fetch"]
    docs = render_tool_docs(toolset.manifests(registry), AgentProfile.REACT_TEXT)
    assert "name: web_fetch" in docs
    assert "name: read_file" not in docs


def test_toolset_pack_and_risk_filter_keep_low_risk_tools() -> None:
    """Pack selection with risk cap should exclude medium/high-risk tools."""
    registry = _registry_with_defaults()
    toolset = ToolSet.packs("filesystem_read", "filesystem_write", "web").with_max_risk(
        ToolRisk.LOW
    )
    filtered = toolset.apply(registry)
    names = filtered.list_names()
    assert "read_file" in names
    assert "web_search" not in names
    assert "file_write" not in names


def test_toolset_supports_discovery_pack() -> None:
    """Discovery pack should include skill/tool/brief/agent helpers."""
    registry = _registry_with_defaults()
    filtered = ToolSet.packs("discovery").apply(registry)
    names = set(filtered.list_names())
    assert {"skill_tool", "tool_search", "brief_tool", "agent_tool"}.issubset(names)
