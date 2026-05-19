"""Tests for built-in subagent request tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.agent import register_agent_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_agent_tool_returns_subagent_request_payload() -> None:
    """agent_tool should return normalized spawn request payload."""
    registry = ToolRegistry()
    register_agent_tools(registry)
    tool = registry.get("agent_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "task": "Summarize recent changes in runtime.",
            "description": "Runtime summary",
            "execution_mode": "background",
            "task_type": "research",
            "idempotency_key": "same-request",
            "metadata": {"priority": "high"},
        }
    )
    request = out["subagent_request"]
    assert request["description"] == "Runtime summary"
    assert request["execution_mode"] == "background"
    assert request["task_type"] == "research"
    assert request["idempotency_key"] == "same-request"
    assert request["metadata"]["priority"] == "high"
    assert request["subagent_run_id"].startswith("subreq_")
    assert request["request_id"] == request["subagent_run_id"]
    assert request["adapter_kind"] == "subagent_orchestration"


@pytest.mark.asyncio
async def test_agent_tool_rejects_invalid_execution_mode() -> None:
    """agent_tool should fail fast for unsupported execution mode."""
    registry = ToolRegistry()
    register_agent_tools(registry)
    tool = registry.get("agent_tool")
    assert tool is not None
    with pytest.raises(ValueError, match="execution_mode"):
        await tool.handler({"task": "x", "description": "d", "execution_mode": "batch"})
