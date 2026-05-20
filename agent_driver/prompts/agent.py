"""Centralized agent prompt templates loaded from package resources."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from typing import Protocol

from agent_driver.tools.builtin.python import python_tool_runtime_facts


@lru_cache(maxsize=None)
def _read_prompt(filename: str) -> str:
    text = resources.files("agent_driver.prompts.templates").joinpath(filename).read_text(
        encoding="utf-8"
    )
    return text.strip()


def react_base_policy() -> str:
    return _read_prompt("react_base_policy.txt")


def react_chat_tool_policy() -> str:
    template = _read_prompt("react_chat_tool_policy.txt")
    if "{current_date}" not in template:
        return template
    return template.format(current_date=datetime.now(UTC).date().isoformat())


def force_final_answer_user_message() -> str:
    return _read_prompt("force_final_answer_user_message.txt")


def force_final_answer_tool_message() -> str:
    return _read_prompt("force_final_answer_tool_message.txt")


class PythonToolSettingsLike(Protocol):
    default_imports: tuple[str, ...]
    allow_overlay: bool
    limits: object
    session_idle_seconds: float


def python_tool_system_addendum(settings: PythonToolSettingsLike) -> str:
    """Render dynamic python-tool system addendum."""
    template = _read_prompt("python_tool_system_addendum.txt")
    required_placeholders = (
        "{imports}",
        "{policy_summary}",
        "{max_exec_ms}",
        "{max_output_chars}",
        "{session_idle_seconds}",
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
    )


__all__ = [
    "force_final_answer_tool_message",
    "force_final_answer_user_message",
    "python_tool_system_addendum",
    "react_base_policy",
    "react_chat_tool_policy",
]
