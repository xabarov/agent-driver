"""Tests for worktree request-envelope tools."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.worktree import register_worktree_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_enter_and_exit_worktree_tools_return_request_payloads() -> None:
    """Worktree tools should return structured request envelopes."""
    registry = ToolRegistry()
    register_worktree_tools(registry)
    enter = registry.get("enter_worktree_tool")
    exit_tool = registry.get("exit_worktree_tool")
    assert enter is not None
    assert exit_tool is not None
    created = await enter.handler({"worktree_name": "feat-x", "base_ref": "main"})
    assert created["worktree_request"]["operation"] == "enter"
    assert created["worktree_request"]["worktree_name"] == "feat-x"
    assert created["worktree_request"]["adapter_kind"] == "worktree"
    assert created["worktree_request"]["request_id"].startswith("wreq_")
    removed = await exit_tool.handler({"worktree_name": "feat-x"})
    assert removed["worktree_request"]["operation"] == "exit"
    assert removed["worktree_request"]["adapter_kind"] == "worktree"
    assert removed["worktree_request"]["request_id"].startswith("wreq_")
