"""Model-free ``Condenser`` tier adapters (compaction hardening C2)."""

from __future__ import annotations

import asyncio

from agent_driver.context.compaction.condenser import CondenseContext, message_chars
from agent_driver.context.compaction.condenser_tiers import (
    PartialCondenser,
    ToolResultPruner,
    default_condenser_tiers,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


def _ctx() -> CondenseContext:
    return CondenseContext(target_chars=1000, extras={"effective_window_tokens": 128000})


def test_default_tiers_order_is_cheapest_first() -> None:
    names = [c.name for c in default_condenser_tiers()]
    assert names == ["tool_result_pruner", "tool_history", "partial"]


def test_tool_result_pruner_frees_old_tool_content() -> None:
    messages = [ChatMessage(role=ChatRole.SYSTEM, content="policy")]
    for i in range(6):
        messages.append(
            ChatMessage(role=ChatRole.TOOL, content="R" * 500, tool_call_id=f"tc{i}")
        )
    before = message_chars(messages)
    result = asyncio.run(ToolResultPruner(keep_recent=3).condense(messages, ctx=_ctx()))
    assert result.changed is True
    assert result.chars_freed > 0
    assert message_chars(result.messages) == before - result.chars_freed
    # Structure preserved: cleared, not dropped.
    assert len(result.messages) == len(messages)


def test_tool_result_pruner_is_noop_when_few_tools() -> None:
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content="policy"),
        ChatMessage(role=ChatRole.TOOL, content="R" * 500, tool_call_id="tc0"),
    ]
    result = asyncio.run(ToolResultPruner(keep_recent=3).condense(messages, ctx=_ctx()))
    assert result.changed is False
    assert result.chars_freed == 0


def test_partial_condenser_is_honest_on_noop() -> None:
    # Below the retain threshold → build_partial_compaction returns a no_op; the
    # adapter must report changed=False, chars_freed=0, and leave the list intact.
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content="policy"),
        ChatMessage(role=ChatRole.USER, content="hi"),
    ]
    result = asyncio.run(PartialCondenser().condense(messages, ctx=_ctx()))
    assert result.changed is False
    assert result.chars_freed == 0
    assert result.messages is messages
