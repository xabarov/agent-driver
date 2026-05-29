"""Guardrail pipeline and output-budget helpers for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_driver.contracts.enums import GuardrailDecision


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    """Result emitted by one guardrail hook."""

    decision: GuardrailDecision = GuardrailDecision.ALLOW
    reason: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def enforce_output_budget(
    summary: str | None, max_chars: int | None
) -> tuple[str | None, bool]:
    """Trim text to configured max chars and return truncation flag."""
    if summary is None or max_chars is None:
        return summary, False
    if len(summary) <= max_chars:
        return summary, False
    if max_chars <= 0:
        return "", True
    return summary[:max_chars], True


class GuardrailPipeline:
    """Hook-based guardrail pipeline with no-op defaults."""

    async def on_input(self, payload: dict[str, Any]) -> GuardrailResult:
        """Inspect run input before model/tool execution."""
        _ = payload
        return GuardrailResult()

    async def on_tool_args(self, payload: dict[str, Any]) -> GuardrailResult:
        """Inspect tool arguments before calling tool handler."""
        _ = payload
        return GuardrailResult()

    async def on_tool_result(self, payload: dict[str, Any]) -> GuardrailResult:
        """Inspect raw tool result before return to runtime."""
        _ = payload
        return GuardrailResult()

    async def on_final_output(self, payload: dict[str, Any]) -> GuardrailResult:
        """Inspect normalized result envelope before trace emission."""
        _ = payload
        return GuardrailResult()


__all__ = ["GuardrailPipeline", "GuardrailResult", "enforce_output_budget"]
