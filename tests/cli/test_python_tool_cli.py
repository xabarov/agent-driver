"""Tests for CLI python tool wiring."""

from __future__ import annotations

import argparse

import pytest

from agent_driver.cli.commands.run_chat import _python_settings_from_args
from agent_driver.cli.tools import CliToolConfig, CliToolConfigError, build_cli_toolset
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.sdk import build_default_registry
from agent_driver.tools.builtin.python import python_tool_runtime_facts


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


def test_no_python_scientific_disables_numpy_in_imports() -> None:
    args = argparse.Namespace(
        enable_python=True,
        python_backend="local",
        python_allow_imports=None,
        no_python_scientific=True,
    )
    settings = _python_settings_from_args(args)
    facts = python_tool_runtime_facts(settings)
    assert "numpy" not in facts.imports_sorted
    assert "scipy" not in facts.imports_sorted
    assert "math" in facts.imports_sorted


def test_enable_python_includes_scientific_by_default() -> None:
    args = argparse.Namespace(
        enable_python=True,
        python_backend="local",
        python_allow_imports=None,
        no_python_scientific=False,
    )
    settings = _python_settings_from_args(args)
    facts = python_tool_runtime_facts(settings)
    assert "numpy" in facts.imports_sorted
    assert "scipy" in facts.imports_sorted
