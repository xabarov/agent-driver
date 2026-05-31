"""Tests for protocol message validation and repair."""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.protocol_validate import (
    validate_and_repair_protocol_messages,
)


def test_validate_coalesces_adjacent_user_messages() -> None:
    result = validate_and_repair_protocol_messages(
        [
            ChatMessage(role=ChatRole.USER, content="first"),
            ChatMessage(role=ChatRole.USER, content="second"),
        ]
    )
    assert len(result.messages) == 1
    assert "first" in (result.messages[0].content or "")
    assert "second" in (result.messages[0].content or "")
    assert "coalesced_adjacent_user_messages" in result.repairs


def test_validate_drops_orphan_tool_message() -> None:
    result = validate_and_repair_protocol_messages(
        [
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ]
                },
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                name="web_search",
                tool_call_id="call_other",
                content="{}",
            ),
        ]
    )
    assert len(result.messages) == 2
    assert result.messages[1].tool_call_id == "call_1"
    assert result.messages[1].metadata["tool_trim_stub"] is True
    assert "dropped_orphan_tool_message" in result.repairs


def test_validate_inserts_stub_for_missing_tool_result() -> None:
    result = validate_and_repair_protocol_messages(
        [
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ]
                },
            )
        ]
    )

    assert [message.role for message in result.messages] == [
        ChatRole.ASSISTANT,
        ChatRole.TOOL,
    ]
    assert result.messages[1].tool_call_id == "call_1"
    assert result.messages[1].name == "web_search"
    assert result.messages[1].metadata["tool_trim_stub"] is True
    assert "inserted_missing_tool_result_stubs" in result.repairs


def test_validate_truncates_oversized_tool_payloads() -> None:
    """Large tool JSON (A/m2-style) should be trimmed before LLM send."""
    huge = "x" * 50_000
    result = validate_and_repair_protocol_messages(
        [
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_fetch", "arguments": "{}"},
                        }
                    ]
                },
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                name="web_fetch",
                tool_call_id="call_1",
                content=huge,
            ),
        ],
        max_total_content_chars=6000,
    )
    total = sum(len(message.content or "") for message in result.messages)
    assert total <= 6000
    assert any(item.startswith("truncated_tool_message") for item in result.repairs)
