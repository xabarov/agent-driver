"""opencode-adoption EPIC-08 — live, pressure-gated ToolResultPruner pre-pass.

Pins ``_apply_live_tool_result_prune`` (runs in ``apply_compaction_if_eligible``
independently of ``enable_compaction``): under token pressure it clears OLD tool-result
content in the ephemeral request keeping the newest ``keep_recent``, commits only above
the char threshold, is a no-op off-pressure / below-threshold / on a second pass, and is
enabled by default.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.context.compaction.tool_clear import CLEARED_MARKER
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _apply_live_tool_result_prune,
)
from agent_driver.runtime.single_agent.types import RunnerConfig


def _host(keep_recent: int = 2, min_chars: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        _config=SimpleNamespace(
            live_tool_prune_keep_recent=keep_recent,
            live_tool_prune_min_chars=min_chars,
        )
    )


def _messages() -> list[ChatMessage]:
    big = "X" * 5000
    return [
        ChatMessage(role=ChatRole.USER, content="hi"),
        ChatMessage(role=ChatRole.TOOL, content=big, tool_call_id="c1"),
        ChatMessage(role=ChatRole.TOOL, content=big, tool_call_id="c2"),
        ChatMessage(role=ChatRole.TOOL, content=big, tool_call_id="c3"),
        ChatMessage(role=ChatRole.TOOL, content=big, tool_call_id="c4"),
    ]


def _request() -> SimpleNamespace:
    return SimpleNamespace(messages=_messages())


def test_prune_fires_under_pressure_keeps_recent() -> None:
    host, ctx, req = _host(), SimpleNamespace(metadata={}), _request()
    _apply_live_tool_result_prune(
        host, context=ctx, request=req, token_pressure_state="blocking"
    )
    tool_contents = [m.content for m in req.messages if m.role == ChatRole.TOOL]
    # keep_recent=2 → the 2 oldest of 4 tool results are cleared, newest 2 intact.
    assert tool_contents[0] == CLEARED_MARKER
    assert tool_contents[1] == CLEARED_MARKER
    assert tool_contents[2] != CLEARED_MARKER
    assert tool_contents[3] != CLEARED_MARKER
    audit = ctx.metadata["live_tool_prune"]
    assert audit["cleared"] == 2
    assert audit["chars_saved"] > 0
    assert audit["token_pressure_state"] == "blocking"


def test_prune_noop_without_pressure() -> None:
    host, ctx, req = _host(), SimpleNamespace(metadata={}), _request()
    before = [m.content for m in req.messages]
    _apply_live_tool_result_prune(
        host, context=ctx, request=req, token_pressure_state="comfortable"
    )
    assert [m.content for m in req.messages] == before
    assert "live_tool_prune" not in ctx.metadata


def test_prune_noop_below_char_threshold() -> None:
    # A huge threshold means the (real) savings never clear the bar → leave untouched.
    host, ctx, req = _host(min_chars=10_000_000), SimpleNamespace(metadata={}), _request()
    before = [m.content for m in req.messages]
    _apply_live_tool_result_prune(
        host, context=ctx, request=req, token_pressure_state="compact_recommended"
    )
    assert [m.content for m in req.messages] == before
    assert "live_tool_prune" not in ctx.metadata


def test_prune_is_idempotent_second_pass_noop() -> None:
    host, ctx, req = _host(), SimpleNamespace(metadata={}), _request()
    _apply_live_tool_result_prune(
        host, context=ctx, request=req, token_pressure_state="blocking"
    )
    ctx.metadata.pop("live_tool_prune", None)
    # already-cleared results are skipped → nothing new to clear → no audit written.
    _apply_live_tool_result_prune(
        host, context=ctx, request=req, token_pressure_state="blocking"
    )
    assert "live_tool_prune" not in ctx.metadata


def test_enabled_by_default() -> None:
    cfg = RunnerConfig()
    assert cfg.live_tool_prune_enabled is True
    assert cfg.live_tool_prune_keep_recent == 3
    assert cfg.live_tool_prune_min_chars == 2000
