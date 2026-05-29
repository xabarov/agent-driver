"""Tests for shell-safety rules in ReAct base policy template."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.llm_step import _react_system_instruction
from agent_driver.tools.registry import ToolRegistry


def test_react_base_policy_template_contains_bash_safety_rule() -> None:
    """Template should explicitly ban shell chaining separators."""
    template_path = Path("agent_driver/prompts/templates/react_base_policy.txt")
    text = template_path.read_text(encoding="utf-8")
    assert "do not use ';', '&&', '||'" in text
    assert "split work into separate bash calls" in text


def test_react_system_instruction_includes_bash_safety_rule() -> None:
    """Composed system prompt should include base shell-safety guidance."""
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=ToolRegistry()),
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
    assert "do not use ';', '&&', '||'" in instruction
