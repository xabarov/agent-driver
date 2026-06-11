"""Tests for optional Deep Research phase hard gate."""

from __future__ import annotations

import pytest

from agent_driver.runtime.deep_research_phase_gate import (
    create_deep_research_phase_gate,
)
from agent_driver.runtime.tool_gate import (
    ToolGateAllow,
    ToolGateContext,
    ToolGateDeny,
)


def _ctx(tool_name: str, args: dict[str, object] | None = None) -> ToolGateContext:
    return ToolGateContext(
        tool_name=tool_name,
        args=args or {},
        run_id="run_test",
        thread_id="thread_test",
        agent_id="agent",
        risk="low",
        side_effect="read",
        current_tool_calls=0,
    )


@pytest.mark.asyncio
async def test_deep_research_phase_gate_allows_expected_sequence() -> None:
    gate = create_deep_research_phase_gate(required_fetch_attempts=2)

    results = [
        await gate(_ctx("todo_write")),
        await gate(_ctx("web_search", {"query": "fork join queue"})),
        await gate(_ctx("web_fetch", {"url": "https://example.com/a"})),
        await gate(_ctx("web_fetch", {"url": "https://example.org/b"})),
        await gate(_ctx("file_write", {"path": "research/report.md"})),
        await gate(_ctx("artifact_preview", {"path": "research/report.md"})),
    ]

    assert all(isinstance(result, ToolGateAllow) for result in results)


@pytest.mark.asyncio
async def test_deep_research_phase_gate_denies_write_before_fetch_attempts() -> None:
    gate = create_deep_research_phase_gate(required_fetch_attempts=2)

    assert isinstance(await gate(_ctx("todo_write")), ToolGateAllow)
    assert isinstance(
        await gate(_ctx("web_search", {"query": "fork join queue"})),
        ToolGateAllow,
    )
    denied = await gate(_ctx("file_write", {"path": "research/report.md"}))

    assert isinstance(denied, ToolGateDeny)
    assert "phase 'verify'" in denied.reason
    assert "web_fetch" in denied.reason


@pytest.mark.asyncio
async def test_deep_research_phase_gate_denies_search_before_todo() -> None:
    gate = create_deep_research_phase_gate(required_fetch_attempts=2)

    denied = await gate(_ctx("web_search", {"query": "fork join queue"}))

    assert isinstance(denied, ToolGateDeny)
    assert "phase 'plan'" in denied.reason
    assert "todo_write" in denied.reason


@pytest.mark.asyncio
async def test_deep_research_phase_gate_allows_agent_tool_after_todo() -> None:
    gate = create_deep_research_phase_gate(required_fetch_attempts=2)

    assert isinstance(await gate(_ctx("todo_write")), ToolGateAllow)
    result = await gate(
        _ctx(
            "agent_tool",
            {
                "description": "Source discovery",
                "task": "Find independent source families for fork-join queues.",
            },
        )
    )

    assert isinstance(result, ToolGateAllow)
