"""Tests for ToolSet filtering and prompt-surface selection."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
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


def test_toolset_supports_artifacts_pack() -> None:
    """Artifacts pack should expose read-only workspace artifact helpers."""
    registry = _registry_with_defaults()
    filtered = ToolSet.packs("artifacts").apply(registry)
    assert set(filtered.list_names()) == {
        "artifact_list",
        "artifact_read",
        "artifact_preview",
    }


def test_toolset_filters_by_application_tags() -> None:
    """Application tags should narrow model-visible and executable surface."""
    registry = _registry_with_defaults()

    async def _sample(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(
            name="tagged_tool",
            description="Tagged custom tool",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            metadata={"application_tags": ["backend", "sdk"]},
        ),
        _sample,
    )
    filtered = ToolSet.all().with_application_tags("sdk").apply(registry)
    assert "tagged_tool" in filtered.list_names()
    filtered_no_match = ToolSet.all().with_application_tags("mobile").apply(registry)
    assert "tagged_tool" not in filtered_no_match.list_names()


def test_toolset_side_effect_filter_limits_surface() -> None:
    """Side-effect class filter should drop non-matching tools."""
    registry = _registry_with_defaults()
    filtered = ToolSet.packs("filesystem_read", "web").with_side_effects(
        SideEffectClass.READ_ONLY
    )
    names = filtered.apply(registry).list_names()
    assert "read_file" in names
    assert "web_search" not in names


def test_toolset_empty_side_effect_and_tag_filters_do_not_drop_tools() -> None:
    """Empty filter inputs should behave as no-op selectors."""
    registry = _registry_with_defaults()
    base = ToolSet.packs("filesystem_read", "planning")
    baseline_names = base.apply(registry).list_names()
    assert baseline_names
    names_with_empty_side_effect = base.with_side_effects().apply(registry).list_names()
    names_with_empty_tags = base.with_application_tags().apply(registry).list_names()
    assert names_with_empty_side_effect == baseline_names
    assert names_with_empty_tags == baseline_names


def test_toolset_without_excludes_named_tools() -> None:
    """without() should drop excluded names after pack composition."""
    registry = _registry_with_defaults()
    names = (
        ToolSet.packs("filesystem_read")
        .without("glob_search")
        .apply(registry)
        .list_names()
    )
    assert "read_file" in names
    assert "glob_search" not in names


def test_toolset_reports_unknown_names_for_validation() -> None:
    """ToolSet should report explicit unknown names deterministically."""
    registry = _registry_with_defaults()
    toolset = ToolSet.only("web_fetch", "missing_tool")
    missing = toolset.unknown_names(registry)
    assert missing == ("missing_tool",)


# ---------------------------------------------------------------------------
# from_preset — coarse-grained governance presets for UI/config layers.
# ---------------------------------------------------------------------------


def test_from_preset_off_selects_no_tools() -> None:
    registry = _registry_with_defaults()
    filtered = ToolSet.from_preset("off").apply(registry)
    assert filtered.list_names() == []


def test_from_preset_safe_keeps_low_risk_read_only_only() -> None:
    registry = _registry_with_defaults()
    filtered = ToolSet.from_preset("safe").apply(registry)
    for registered in filtered.list_registered():
        manifest = registered.manifest
        assert manifest.risk == ToolRisk.LOW, manifest.name
        assert manifest.side_effect in (
            SideEffectClass.NONE,
            SideEffectClass.READ_ONLY,
        ), f"{manifest.name}: side_effect={manifest.side_effect}"


def test_from_preset_dev_excludes_irreversible_and_external() -> None:
    registry = _registry_with_defaults()
    filtered = ToolSet.from_preset("dev").apply(registry)
    for registered in filtered.list_registered():
        manifest = registered.manifest
        # No HIGH-risk tools in dev preset.
        assert manifest.risk != ToolRisk.HIGH, manifest.name
        # No irreversible / external-action side-effects.
        assert manifest.side_effect not in (
            SideEffectClass.IRREVERSIBLE_WRITE,
            SideEffectClass.EXTERNAL_ACTION,
        ), f"{manifest.name}: {manifest.side_effect}"


def test_from_preset_all_matches_all_factory() -> None:
    registry = _registry_with_defaults()
    a = ToolSet.from_preset("all").apply(registry).list_names()
    b = ToolSet.all().apply(registry).list_names()
    assert sorted(a) == sorted(b)


def test_from_preset_normalizes_whitespace_and_case() -> None:
    registry = _registry_with_defaults()
    a = ToolSet.from_preset("  SAFE  ").apply(registry).list_names()
    b = ToolSet.from_preset("safe").apply(registry).list_names()
    assert sorted(a) == sorted(b)


def test_from_preset_rejects_unknown_name_with_value_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown ToolSet preset 'wat'"):
        ToolSet.from_preset("wat")


def test_from_preset_safe_preserves_zero_disclosure_when_no_low_read_only() -> None:
    """Empty source registry → empty preset surface (no exception)."""
    empty = ToolRegistry()
    filtered = ToolSet.from_preset("safe").apply(empty)
    assert filtered.list_names() == []
