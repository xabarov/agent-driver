"""Guard tests for python tool prompt template placeholders."""

from __future__ import annotations

from importlib import resources

from agent_driver.prompts.agent import python_tool_system_addendum
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings


def test_python_tool_system_addendum_template_has_required_placeholders() -> None:
    text = (
        resources.files("agent_driver.prompts.templates")
        .joinpath("python_tool_system_addendum.txt")
        .read_text(encoding="utf-8")
    )
    assert "{imports}" in text
    assert "{policy_summary}" in text
    assert "{max_exec_ms}" in text
    assert "{max_output_chars}" in text
    assert "{session_idle_seconds}" in text
    assert "{scientific_guidance}" in text
    assert "not missing package" in text.lower() or "NOT" in text


def test_python_tool_system_addendum_scientific_on_guidance() -> None:
    rendered = python_tool_system_addendum(
        PythonToolSettings(enabled=True, include_scientific_stack=True)
    )
    assert "Allowed imports:" in rendered
    assert "numpy" in rendered
    assert "scipy.stats" in rendered
    assert "pandas" in rendered


def test_python_tool_system_addendum_scientific_off_negative_list() -> None:
    rendered = python_tool_system_addendum(
        PythonToolSettings(
            enabled=True,
            include_scientific_stack=False,
            default_imports=(),
        )
    )
    assert "NOT available" in rendered or "NOT" in rendered
    assert "Do not import scipy.stats" in rendered
    assert "math" in rendered
