"""Guard tests for python tool prompt template placeholders."""

from __future__ import annotations

from importlib import resources


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
