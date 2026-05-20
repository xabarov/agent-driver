"""Centralized agent prompt templates loaded from package resources."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from typing import Protocol

from agent_driver.tools.builtin.python import python_tool_runtime_facts
from agent_driver.tools.builtin.python_imports import scientific_imports_enabled


@lru_cache(maxsize=None)
def _read_prompt(filename: str) -> str:
    text = resources.files("agent_driver.prompts.templates").joinpath(filename).read_text(
        encoding="utf-8"
    )
    return text.strip()


def react_base_policy() -> str:
    return _read_prompt("react_base_policy.txt")


def react_chat_tool_policy(*, include_scientific_python: bool = False) -> str:
    template = _read_prompt("react_chat_tool_policy.txt")
    if include_scientific_python:
        scientific_note = (
            "numpy, scipy, and pandas are available in the python allowlist when enabled; "
            "prefer scipy.stats for distributions and pandas for tabular data."
        )
    else:
        scientific_note = (
            "Do not assume numpy, scipy, or pandas are installed unless listed in python policy."
        )
    format_kwargs: dict[str, str] = {"python_scientific_note": scientific_note}
    if "{current_date}" in template:
        format_kwargs["current_date"] = datetime.now(UTC).date().isoformat()
    return template.format(**format_kwargs)


def todo_write_guidance() -> str:
    """Static guidance for todo_write when the tool is registered."""
    return _read_prompt("todo_write_guidance.txt")


def force_final_answer_user_message() -> str:
    return _read_prompt("force_final_answer_user_message.txt")


def force_final_answer_tool_message() -> str:
    return _read_prompt("force_final_answer_tool_message.txt")


class PythonToolSettingsLike(Protocol):
    include_scientific_stack: bool
    default_imports: tuple[str, ...]
    allow_overlay: bool
    limits: object
    session_idle_seconds: float


def _scientific_guidance_block(settings: PythonToolSettingsLike) -> str:
    if scientific_imports_enabled(settings):
        return (
            "- numpy, scipy, and pandas are available in the allowlist; "
            "prefer scipy.stats for gamma CDF/tail probabilities and pandas for tabular data.\n"
            "- Do not claim scientific packages are missing when they are listed above."
        )
    return (
        "- Third-party packages (numpy, scipy, pandas, sklearn, sympy, etc.) are NOT "
        "available unless explicitly listed under Allowed imports above.\n"
        "- For statistics (gamma CDF, tail probabilities, moment fitting): use `math` "
        "and/or `statistics` from the allowlist, or pure formulas. "
        "Do not import scipy.stats or numpy."
    )


def python_tool_system_addendum(settings: PythonToolSettingsLike) -> str:
    """Render dynamic python-tool system addendum."""
    template = _read_prompt("python_tool_system_addendum.txt")
    required_placeholders = (
        "{imports}",
        "{policy_summary}",
        "{max_exec_ms}",
        "{max_output_chars}",
        "{session_idle_seconds}",
        "{scientific_guidance}",
    )
    if any(placeholder not in template for placeholder in required_placeholders):
        raise RuntimeError(
            "python_tool_system_addendum template requires "
            + ", ".join(required_placeholders)
            + " placeholders"
        )
    facts = python_tool_runtime_facts(settings)
    return template.format(
        imports=facts.imports_inline,
        policy_summary=facts.policy_summary,
        max_exec_ms=facts.max_exec_ms,
        max_output_chars=facts.max_output_chars,
        session_idle_seconds=facts.session_idle_seconds,
        scientific_guidance=_scientific_guidance_block(settings),
    )


__all__ = [
    "force_final_answer_tool_message",
    "force_final_answer_user_message",
    "python_tool_system_addendum",
    "react_base_policy",
    "react_chat_tool_policy",
    "todo_write_guidance",
]
