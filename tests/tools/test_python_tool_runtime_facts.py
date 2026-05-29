"""Tests for python tool runtime facts helper."""

from __future__ import annotations

from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.tools.builtin.python import PythonToolRuntimeFacts, python_tool_runtime_facts


def test_python_tool_runtime_facts_are_sorted() -> None:
    settings = PythonToolSettings(enabled=True, default_imports=("re", "math", "re"))
    facts = python_tool_runtime_facts(settings)
    assert isinstance(facts, PythonToolRuntimeFacts)
    assert facts.imports_sorted == ("math", "re")
    assert facts.imports_inline == "math, re"


def test_python_tool_runtime_facts_short_preview_has_suffix() -> None:
    settings = PythonToolSettings(
        enabled=True,
        default_imports=tuple(f"mod{i}" for i in range(11)),
        limits=CodeAgentLimits(),
    )
    facts = python_tool_runtime_facts(settings)
    assert "+3 more" in facts.imports_short
