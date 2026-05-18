"""CodeAgent prompt rendering tests."""

from __future__ import annotations

from agent_driver.code_agent import render_code_agent_prompt


def test_render_code_agent_prompt_contains_required_sections() -> None:
    """Prompt renderer should include task/imports/tools/final-answer contract."""
    rendered = render_code_agent_prompt(
        task="Compute 2 + 2",
        tool_docs="def lookup(query: object) -> dict[str, object]",
        authorized_imports=("math", "datetime"),
        observations=["[tool_stdout] value=2"],
        clarification="Use safe approach",
    )
    text = rendered.rendered_text
    assert "final_answer(...)" in text
    assert "Authorized imports:" in text
    assert "datetime, math" in text
    assert "Callable tools:" in text
    assert "Compute 2 + 2" in text
    assert len(rendered.rendered_hash) == 64


def test_render_code_agent_prompt_is_deterministic() -> None:
    """Prompt rendering must be stable for identical inputs."""
    first = render_code_agent_prompt(
        task="A",
        tool_docs="",
        authorized_imports=("math",),
        observations=[],
        clarification=None,
    )
    second = render_code_agent_prompt(
        task="A",
        tool_docs="",
        authorized_imports=("math",),
        observations=[],
        clarification=None,
    )
    assert first.rendered_text == second.rendered_text
    assert first.rendered_hash == second.rendered_hash
