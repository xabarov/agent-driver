"""Tests for built-in session-local messaging tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.messaging import (
    _reset_message_store_for_tests,
    register_messaging_tools,
)
from agent_driver.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    _reset_message_store_for_tests()


@pytest.mark.asyncio
async def test_send_message_tool_queues_message_event() -> None:
    """send_message_tool should enqueue a normalized message event."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    tool = registry.get("send_message_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "recipient": "agent.teammate",
            "message": "Please validate the summary.",
            "thread_id": "thread_a",
            "channel": "direct",
            "metadata": {"priority": "high"},
        }
    )
    event = out["message_event"]
    assert event["recipient"] == "agent.teammate"
    assert event["thread_id"] == "thread_a"
    assert event["channel"] == "direct"
    assert event["metadata"]["priority"] == "high"
    assert event["message_event_id"].startswith("msg_")
    assert event["event_id"] == event["message_event_id"]
    assert event["adapter_kind"] == "collaboration"
    assert event["provenance"]["source_tool"] == "send_message_tool"


@pytest.mark.asyncio
async def test_send_message_tool_rejects_invalid_channel() -> None:
    """send_message_tool should reject unsupported channel values."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    tool = registry.get("send_message_tool")
    assert tool is not None
    with pytest.raises(ValueError, match="channel"):
        await tool.handler({"recipient": "a", "message": "x", "channel": "room"})


@pytest.mark.asyncio
async def test_list_peers_tool_filters_by_capability_and_status() -> None:
    """list_peers_tool should support deterministic status/capability filtering."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    tool = registry.get("list_peers_tool")
    assert tool is not None
    out = await tool.handler({"status": "online", "capability": "summary"})
    peers = out["peers"]
    assert peers
    assert all(item["status"] == "online" for item in peers)
    assert all("summary" in item["capabilities"] for item in peers)


@pytest.mark.asyncio
async def test_list_peers_tool_excludes_offline_by_default() -> None:
    """list_peers_tool should exclude offline peers unless explicitly requested."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    tool = registry.get("list_peers_tool")
    assert tool is not None
    out_default = await tool.handler({})
    assert all(item["status"] != "offline" for item in out_default["peers"])
    out_full = await tool.handler({"include_offline": True})
    assert any(item["status"] == "offline" for item in out_full["peers"])


@pytest.mark.asyncio
async def test_team_create_and_delete_tools_manage_team_rows() -> None:
    """team_create_tool and team_delete_tool should manage session-local teams."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    create = registry.get("team_create_tool")
    delete = registry.get("team_delete_tool")
    assert create is not None
    assert delete is not None
    created = await create.handler(
        {
            "team_id": "team_alpha",
            "display_name": "Alpha Team",
            "members": ["agent.teammate", "agent.researcher"],
            "purpose": "Review and summarize",
        }
    )
    team = created["team"]
    assert team["team_id"] == "team_alpha"
    assert team["display_name"] == "Alpha Team"
    assert "agent.teammate" in team["members"]
    assert team["adapter_kind"] == "collaboration"
    assert team["event_id"].startswith("team_")
    removed = await delete.handler({"team_id": "team_alpha"})
    assert removed["deleted_team"]["team_id"] == "team_alpha"


@pytest.mark.asyncio
async def test_team_create_rejects_duplicate_team_id() -> None:
    """team_create_tool should reject duplicate team ids."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    create = registry.get("team_create_tool")
    assert create is not None
    await create.handler({"team_id": "team_dup"})
    with pytest.raises(ValueError, match="already exists"):
        await create.handler({"team_id": "team_dup"})


@pytest.mark.asyncio
async def test_team_get_and_list_tools_return_expected_rows() -> None:
    """team_get_tool and team_list_tool should return deterministic team payloads."""
    registry = ToolRegistry()
    register_messaging_tools(registry)
    create = registry.get("team_create_tool")
    get_tool = registry.get("team_get_tool")
    list_tool = registry.get("team_list_tool")
    assert create is not None
    assert get_tool is not None
    assert list_tool is not None
    await create.handler(
        {
            "team_id": "team_get_a",
            "members": ["agent.teammate", "agent.researcher"],
        }
    )
    await create.handler(
        {
            "team_id": "team_get_b",
            "members": ["agent.writer"],
        }
    )
    loaded = await get_tool.handler({"team_id": "team_get_a"})
    assert loaded["team"]["team_id"] == "team_get_a"
    listed = await list_tool.handler({"member": "agent.teammate"})
    teams = listed["teams"]
    assert len(teams) == 1
    assert teams[0]["team_id"] == "team_get_a"
