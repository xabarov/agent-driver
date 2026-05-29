"""Tests for python builtin tool manifest."""

from __future__ import annotations

from agent_driver.contracts.enums import AgentProfile, SideEffectClass, ToolRisk
from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.tools.builtin.python import build_python_tool_manifest, python_tool_manifest


def test_python_tool_manifest_shape() -> None:
    manifest = python_tool_manifest()
    assert manifest.name == "python"
    assert manifest.risk == ToolRisk.MEDIUM
    assert manifest.side_effect == SideEffectClass.READ_ONLY
    assert manifest.idempotent is False
    assert manifest.args_schema is not None
    assert manifest.args_schema.get("required") == ["code"]
    assert AgentProfile.TOOL_CALLING in manifest.supported_profiles
    assert AgentProfile.REACT_TEXT in manifest.supported_profiles
    assert AgentProfile.CODE_AGENT in manifest.supported_profiles


def test_python_tool_manifest_reflects_runtime_imports() -> None:
    settings = PythonToolSettings(enabled=True, default_imports=("math", "re"))
    manifest = build_python_tool_manifest(settings)
    assert "Allowed imports: math, re" in manifest.description
    args_schema = manifest.args_schema or {}
    code_desc = (
        args_schema.get("properties", {}).get("code", {}).get("description", "")
    )
    assert "math, re" in code_desc


def test_python_tool_manifest_shortens_long_import_list() -> None:
    settings = PythonToolSettings(
        enabled=True,
        default_imports=tuple(f"mod{i}" for i in range(20)),
        limits=CodeAgentLimits(),
    )
    manifest = build_python_tool_manifest(settings)
    args_schema = manifest.args_schema or {}
    code_desc = (
        args_schema.get("properties", {}).get("code", {}).get("description", "")
    )
    assert "+12 more" in code_desc


def test_python_tool_manifest_hides_overlay_field_when_disabled() -> None:
    settings = PythonToolSettings(enabled=True, allow_overlay=False)
    manifest = build_python_tool_manifest(settings)
    properties = (manifest.args_schema or {}).get("properties", {})
    assert "authorized_imports" not in properties
