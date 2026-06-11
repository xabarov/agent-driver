"""Token-usage projection from ``AgentRunOutput.usage`` to the wire shapes.

Two intentional variants — OpenAI's two surfaces name the same numbers
differently: chat completions / runs use ``prompt_tokens`` /
``completion_tokens``; the Responses API uses ``input_tokens`` /
``output_tokens``. Keeping both here (instead of inline copies) makes the
divergence explicit rather than accidental.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_driver.contracts.runtime import AgentRunOutput


def _tokens(usage: object) -> tuple[int, int, int]:
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0) or (inp + out)
    return inp, out, total


def chat_usage(output: "AgentRunOutput") -> dict[str, int]:
    """Usage for chat completions / runs (``prompt``/``completion`` names)."""
    inp, out, total = _tokens(output.usage)
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": total}


def responses_usage(output: "AgentRunOutput") -> dict[str, int] | None:
    """Usage for the Responses API (``input``/``output`` names); None if absent."""
    if output.usage is None:
        return None
    inp, out, total = _tokens(output.usage)
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


__all__ = ["chat_usage", "responses_usage"]
