"""CodeAgent action parser tests."""

from __future__ import annotations

import pytest

from agent_driver.code_agent.parse import parse_code_action
from agent_driver.contracts import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse


def _response(content: str, metadata: dict[str, object] | None = None) -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=LlmFinishReason.STOP,
        provider="fake",
        model="fake-model",
        metadata=metadata or {},
    )


def test_parse_prefers_metadata_code_action() -> None:
    """Parser should use metadata code_action when present."""
    action = parse_code_action(
        _response(
            "```python\nfinal_answer(1)\n```",
            metadata={"code_action": "final_answer(2)"},
        )
    )
    assert action is not None
    assert action.code == "final_answer(2)"


def test_parse_reads_single_fenced_code_block() -> None:
    """Parser should extract one fenced python block."""
    action = parse_code_action(
        _response("Here is code:\n```python\nx=1\nfinal_answer(x)\n```")
    )
    assert action is not None
    assert "final_answer" in action.code


def test_parse_returns_none_without_action() -> None:
    """Parser should return None when no metadata or fenced code exists."""
    assert parse_code_action(_response("no code here")) is None


def test_parse_rejects_multiple_non_empty_blocks() -> None:
    """Parser should reject ambiguous multi-block payloads."""
    with pytest.raises(ValueError, match="exactly one"):
        parse_code_action(
            _response("```python\nx=1\n```\n\n```python\nfinal_answer(x)\n```")
        )


def test_parse_rejects_empty_metadata_action() -> None:
    """Parser should fail when metadata action is empty string."""
    with pytest.raises(ValueError, match="empty"):
        parse_code_action(_response("ignored", metadata={"code_action": "   "}))
