"""AD-1: exit_plan_mode_v2 preserves ordered exact action envelopes.

The generic plan artifact must carry repeated calls of one tool with
different arguments, in order, integrity-bound - so the approving host can
execute the reviewed intent instead of reinventing it from categories.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from agent_driver.tools.planning import register_planning_tool
from agent_driver.tools.registry import ToolRegistry

_ACTIONS = [
    {
        "tool": "web_request",
        "args": {"url": "http://x.test/robots.txt", "method": "GET"},
        "title": "robots disclosure",
    },
    {
        "tool": "web_request",
        "args": {"url": "http://x.test/security.txt", "method": "GET"},
        "title": "security.txt disclosure",
    },
    {
        "tool": "ffuf",
        "args": {"profile_id": "content_paths.small.v1"},
        "title": "content discovery",
    },
]


@pytest.mark.asyncio
async def test_exact_actions_preserved_in_order_with_integrity_hash() -> None:
    registry = ToolRegistry()
    register_planning_tool(registry)
    exit_v2 = registry.get("exit_plan_mode_v2")
    assert exit_v2 is not None

    exited = await exit_v2.handler(
        {
            "reason": "disclosure pass",
            "content": "1. robots 2. security.txt 3. content discovery",
            "requested_tools": ["web_request", "ffuf"],
            "target_urls": ["http://x.test/"],
            "actions": _ACTIONS,
        }
    )

    approval = exited["plan_approval"]
    assert approval["actions"] == _ACTIONS
    canonical = json.dumps(_ACTIONS, sort_keys=True, separators=(",", ":"))
    assert (
        approval["actions_sha256"]
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    assert exited["plan"]["actions"] == _ACTIONS


@pytest.mark.asyncio
async def test_actions_derive_requested_tools_when_omitted() -> None:
    registry = ToolRegistry()
    register_planning_tool(registry)
    exit_v2 = registry.get("exit_plan_mode_v2")

    exited = await exit_v2.handler(
        {
            "content": "1. robots 2. security.txt",
            "target_urls": ["http://x.test/"],
            "actions": _ACTIONS[:2],
        }
    )
    approval = exited["plan_approval"]
    assert approval["requested_tools"] == ["web_request"]


@pytest.mark.asyncio
async def test_action_entries_are_validated() -> None:
    registry = ToolRegistry()
    register_planning_tool(registry)
    exit_v2 = registry.get("exit_plan_mode_v2")

    with pytest.raises(ValueError, match=r"actions\[0\]\.tool is required"):
        await exit_v2.handler(
            {
                "content": "plan",
                "target_urls": ["http://x.test/"],
                "actions": [{"args": {"url": "http://x.test/"}}],
            }
        )

    with pytest.raises(ValueError, match=r"actions\[0\]\.args must be an object"):
        await exit_v2.handler(
            {
                "content": "plan",
                "target_urls": ["http://x.test/"],
                "actions": [{"tool": "web_request", "args": "http://x.test/"}],
            }
        )


@pytest.mark.asyncio
async def test_no_actions_keeps_legacy_shape() -> None:
    registry = ToolRegistry()
    register_planning_tool(registry)
    exit_v2 = registry.get("exit_plan_mode_v2")

    exited = await exit_v2.handler(
        {
            "content": "1. Inspect",
            "requested_tools": ["file_write"],
            "target_urls": ["file:///workspace"],
        }
    )
    approval = exited["plan_approval"]
    assert approval["actions"] == []
    assert approval["actions_sha256"] == hashlib.sha256(b"[]").hexdigest()
