"""Tests for CLI python tool wiring."""

from __future__ import annotations

import pytest

from agent_driver.cli.tools import CliToolConfig, CliToolConfigError, build_cli_toolset
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.sdk import build_default_registry


def test_python_pack_requires_dangerous_opt_in_without_shortcut() -> None:
    with pytest.raises(CliToolConfigError, match="dangerous tool packs"):
        build_cli_toolset(CliToolConfig(tool_packs=("python_exec",)))


def test_enable_python_shortcut_selects_python_tool() -> None:
    registry = build_default_registry(
        RunnerConfig(
            python_tool=PythonToolSettings(enabled=True, backend="local"),
        )
    )
    toolset = build_cli_toolset(CliToolConfig(enable_python=True))
    names = {manifest.name for manifest in toolset.manifests(registry)}
    assert "python" in names
