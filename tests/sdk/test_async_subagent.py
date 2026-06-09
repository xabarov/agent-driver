"""D6: in-process background subagents (start / check / cancel)."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import AsyncSubagentManager, ToolSet, create_agent
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec


def _parent(answer: str = "child-done"):
    return create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )


@pytest.mark.asyncio
async def test_start_check_result() -> None:
    mgr = AsyncSubagentManager(parent=_parent("hi from child"))
    handle = mgr.start(SubagentSpec(agent_type="bg", prompt="go"))
    assert mgr.get(handle.task_id) is handle
    assert handle.task_id in {h.task_id for h in mgr.list_tasks()}

    result = await handle.result()
    assert isinstance(result, SubagentResult)
    assert result.answer == "hi from child"
    assert result.agent_type == "bg"
    assert handle.status() == "done"
    assert handle.done() is True
    assert handle.result_if_ready() is result


@pytest.mark.asyncio
async def test_gather_all() -> None:
    mgr = AsyncSubagentManager(parent=_parent("ok"))
    mgr.start(SubagentSpec(agent_type="a", prompt="x"))
    mgr.start(SubagentSpec(agent_type="b", prompt="y"))
    results = await mgr.gather()
    assert len(results) == 2
    assert all(
        r is not None and r.status.value == "completed" for r in results.values()
    )


@pytest.mark.asyncio
async def test_cancel_marks_cancelled() -> None:
    mgr = AsyncSubagentManager(parent=_parent("slow"))
    handle = mgr.start(SubagentSpec(agent_type="bg", prompt="go"))
    assert mgr.cancel(handle.task_id) is True
    with pytest.raises(asyncio.CancelledError):
        await handle.result()
    assert handle.status() == "cancelled"
    assert handle.result_if_ready() is None


def test_cancel_unknown_task_returns_false() -> None:
    mgr = AsyncSubagentManager(parent=_parent())
    assert mgr.cancel("nope") is False
    assert mgr.get("nope") is None
