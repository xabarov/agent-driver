"""Epic 044 A: per-category context breakdown; total matches the compaction heuristic."""

from __future__ import annotations

from agent_driver.context.breakdown import (
    CONTEXT_BREAKDOWN_CATEGORIES,
    estimate_context_breakdown,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import scaffolding_metadata


def _messages() -> list[ChatMessage]:
    return [
        ChatMessage(role=ChatRole.SYSTEM, content="S" * 40),  # system_prompt
        ChatMessage(role=ChatRole.USER, content="U" * 20),  # conversation
        ChatMessage(role=ChatRole.ASSISTANT, content="A" * 20),  # conversation
        ChatMessage(role=ChatRole.TOOL, content="T" * 80, tool_call_id="c1"),  # tool_results
        ChatMessage(
            role=ChatRole.USER,
            content="N" * 16,
            metadata=scaffolding_metadata("todo_reminder"),  # scaffolding
        ),
    ]


def test_categorizes_by_role_and_scaffolding_marker() -> None:
    out = estimate_context_breakdown(_messages())
    cats = out["categories"]
    assert set(cats) == set(CONTEXT_BREAKDOWN_CATEGORIES)
    assert cats["system_prompt"]["chars"] == 40
    assert cats["conversation"]["chars"] == 40  # user + assistant
    assert cats["tool_results"]["chars"] == 80
    assert cats["scaffolding"]["chars"] == 16  # tagged turn NOT counted as conversation
    assert cats["tool_definitions"]["chars"] == 0
    assert cats["message_metadata"]["chars"] > 0


def test_total_tokens_matches_chars_over_four() -> None:
    # The authoritative total equals the same (total_chars // 4) the compaction
    # trigger uses — UI number and trigger never disagree.
    out = estimate_context_breakdown(_messages())
    assert out["total_chars"] == (
        40 + 20 + 20 + 80 + 16 + out["categories"]["message_metadata"]["chars"]
    )
    assert out["total_tokens"] == out["total_chars"] // 4


def test_tool_definitions_estimated_from_schemas() -> None:
    tools = [
        {"type": "function", "function": {"name": "web_search", "parameters": {}}},
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
    ]
    out = estimate_context_breakdown([], tools=tools)
    assert out["categories"]["tool_definitions"]["chars"] > 0
    assert out["total_tokens"] == out["total_chars"] // 4


def test_accepts_serialized_message_dicts() -> None:
    dicts = [m.model_dump(mode="json") for m in _messages()]
    from_objs = estimate_context_breakdown(_messages())
    from_dicts = estimate_context_breakdown(dicts)
    assert from_dicts["categories"] == from_objs["categories"]


def test_empty_input_is_all_zero() -> None:
    out = estimate_context_breakdown([])
    assert out["total_chars"] == 0
    assert out["total_tokens"] == 0
    assert all(c["chars"] == 0 for c in out["categories"].values())


def test_total_matches_token_pressure_for_same_request() -> None:
    from agent_driver.context.token_pressure import (
        TokenPressureInput,
        estimate_token_pressure,
    )

    tools = [
        {"type": "function", "function": {"name": "lookup", "parameters": {}}}
    ]
    messages = [message.model_dump(mode="json") for message in _messages()]
    breakdown = estimate_context_breakdown(messages, tools=tools)
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=tuple(messages),
            tool_schemas=tuple(tools),
        )
    )
    assert pressure["total_chars"] == breakdown["total_chars"]
    assert pressure["used_tokens_estimate"] == breakdown["total_tokens"]
