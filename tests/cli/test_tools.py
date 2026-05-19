"""Tests for CLI tool-surface configuration."""

from __future__ import annotations

import pytest

from agent_driver.cli.tools import CliToolConfig, CliToolConfigError, build_cli_toolset
from agent_driver.contracts.enums import ToolRisk
from agent_driver.sdk import build_default_registry


def test_default_toolset_is_non_empty_and_safe() -> None:
    """Default CLI toolset should include useful read/web/planning tools only."""
    registry = build_default_registry()
    toolset = build_cli_toolset(CliToolConfig())
    names = {manifest.name for manifest in toolset.manifests(registry)}
    assert names
    assert "read_file" in names
    assert "web_search" in names
    assert "planning_state_update" in names
    assert "bash" not in names
    assert "file_write" not in names


def test_tools_none_keeps_empty_surface() -> None:
    """`--tools none` should keep empty tool surface."""
    registry = build_default_registry()
    toolset = build_cli_toolset(CliToolConfig(tools_mode="none"))
    assert toolset.manifests(registry) == []


def test_shell_pack_requires_explicit_dangerous_opt_in() -> None:
    """Dangerous packs should require allow-dangerous-tools."""
    with pytest.raises(CliToolConfigError, match="dangerous tool packs"):
        _ = build_cli_toolset(CliToolConfig(tool_packs=("shell",)))


def test_tools_all_requires_explicit_dangerous_opt_in() -> None:
    """`--tools all` should be gated as dangerous by default."""
    with pytest.raises(CliToolConfigError, match="--tools all requires"):
        _ = build_cli_toolset(CliToolConfig(tools_mode="all"))


def test_max_risk_filter_is_applied() -> None:
    """Max risk cap should apply to selected tool manifests."""
    registry = build_default_registry()
    toolset = build_cli_toolset(
        CliToolConfig(
            tools_mode="default",
            max_tool_risk=ToolRisk.LOW.value,
        )
    )
    manifests = toolset.manifests(registry)
    assert manifests
    assert all(manifest.risk == ToolRisk.LOW for manifest in manifests)
