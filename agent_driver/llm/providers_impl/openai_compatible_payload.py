"""Compatibility shim for OpenAI-compatible payload helpers."""

from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
    normalize_tool_choice_for_openai,
)

__all__ = [
    "build_openai_completion_payload",
    "normalize_tool_choice_for_openai",
]
