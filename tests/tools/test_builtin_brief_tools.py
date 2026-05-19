"""Tests for built-in runtime brief tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.brief import register_brief_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_brief_tool_returns_message_and_attachment_refs() -> None:
    """brief_tool should return normalized attachment refs."""
    registry = ToolRegistry()
    register_brief_tools(registry)
    tool = registry.get("brief_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "message": "Build status attached",
            "channel": "status",
            "attachments": [
                {
                    "artifact_id": "art_123",
                    "kind": "tool_result",
                    "sensitivity": "internal",
                    "label": "build.log",
                }
            ],
        }
    )
    brief = out["brief"]
    assert brief["channel"] == "status"
    assert brief["message"] == "Build status attached"
    assert brief["attachments"][0]["artifact_ref"]["artifact_id"] == "art_123"
    assert brief["attachments"][0]["artifact_ref"]["kind"] == "tool_result"
    assert brief["attachments"][0]["label"] == "build.log"


@pytest.mark.asyncio
async def test_brief_tool_truncates_message_by_limit() -> None:
    """brief_tool should truncate long message at max_message_chars."""
    registry = ToolRegistry()
    register_brief_tools(registry)
    tool = registry.get("brief_tool")
    assert tool is not None
    out = await tool.handler({"message": "x" * 100, "max_message_chars": 32})
    brief = out["brief"]
    assert len(brief["message"]) == 32
    assert brief["truncated"] is True
