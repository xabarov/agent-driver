"""Tests for ReAct system instruction composition."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts import ToolManifest
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.llm_step import (
    _effective_code_agent_imports,
    _react_system_instruction,
)
from agent_driver.tools.registry import ToolRegistry


def test_react_system_prompt_contains_todo_status_rules() -> None:
    """ReAct profile should always receive base system instruction."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "pending, in_progress, completed, cancelled" in instruction
    assert "counting/listing questions" in instruction


def test_react_system_prompt_includes_workspace_cwd_when_present() -> None:
    """Workspace hint should be appended when app metadata has workspace_cwd."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"workspace_cwd": "/tmp/workspace"},
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Workspace cwd: /tmp/workspace" in instruction


def test_react_system_prompt_includes_python_addendum_when_tool_enabled() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolManifest(name="python", description="python tool"),
        lambda _args: {},
    )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(
            python_tool=PythonToolSettings(
                enabled=True,
                default_imports=("math", "re"),
            )
        ),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Python tool sandbox policy:" in instruction
    assert "Allowed imports: math, re" in instruction
    assert "Limits: exec <=" in instruction
    assert "Sessions are persistent per session_id" in instruction


def test_react_system_prompt_omits_python_addendum_when_disabled() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolManifest(name="python", description="python tool"),
        lambda _args: {},
    )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Python tool sandbox policy:" not in instruction


def test_effective_code_agent_imports_fallback_to_python_tool_defaults() -> None:
    host = SimpleNamespace(
        _config=SimpleNamespace(
            authorized_imports=tuple(),
            python_tool=PythonToolSettings(enabled=True, default_imports=("math", "re")),
        )
    )
    assert _effective_code_agent_imports(host) == ("math", "re")
