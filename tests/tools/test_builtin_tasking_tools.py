"""Tests for built-in task/monitor tools."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.tasking import (
    _reset_task_store_for_tests,
    register_tasking_tools,
)
from agent_driver.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_task_store() -> None:
    _reset_task_store_for_tests()


@pytest.mark.asyncio
async def test_task_create_get_update_output_and_list_flow() -> None:
    """Task tools should provide deterministic lifecycle with bounded output."""
    registry = ToolRegistry()
    register_tasking_tools(registry)
    create = registry.get("task_create")
    get = registry.get("task_get")
    update = registry.get("task_update")
    task_output = registry.get("task_output")
    list_tool = registry.get("task_list")
    assert create is not None
    assert get is not None
    assert update is not None
    assert task_output is not None
    assert list_tool is not None

    created = await create.handler({"title": "ingest batch", "metadata": {"lane": "a"}})
    task_id = created["task"]["task_id"]
    assert created["task"]["status"] == "running"

    listed = await list_tool.handler({})
    assert len(listed["tasks"]) == 1
    assert listed["tasks"][0]["task_id"] == task_id

    await task_output.handler(
        {"task_id": task_id, "source": "stdout", "text": "x" * 5000, "max_chars": 128}
    )
    loaded = await get.handler({"task_id": task_id, "include_output": True})
    output = loaded["task"]["output"]
    assert len(output) == 1
    assert output[0]["source"] == "stdout"
    assert len(output[0]["text_preview"]) == 128

    updated = await update.handler({"task_id": task_id, "status": "completed"})
    assert updated["task"]["status"] == "completed"
    listed_completed = await list_tool.handler({"status": "completed"})
    assert len(listed_completed["tasks"]) == 1
    assert listed_completed["tasks"][0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_task_create_rejects_duplicate_running_title() -> None:
    """Task create should reject duplicate running title."""
    registry = ToolRegistry()
    register_tasking_tools(registry)
    create = registry.get("task_create")
    assert create is not None
    await create.handler({"title": "same"})
    with pytest.raises(ValueError, match="same title"):
        await create.handler({"title": "same"})


@pytest.mark.asyncio
async def test_task_get_rejects_unknown_id() -> None:
    """Task get should fail fast for unknown task id."""
    registry = ToolRegistry()
    register_tasking_tools(registry)
    get = registry.get("task_get")
    assert get is not None
    with pytest.raises(ValueError, match="unknown task_id"):
        await get.handler({"task_id": "missing"})


@pytest.mark.asyncio
async def test_task_stop_monitor_and_sleep_tools() -> None:
    """task_stop, monitor, sleep should provide bounded deterministic behavior."""
    registry = ToolRegistry()
    register_tasking_tools(registry)
    create = registry.get("task_create")
    output = registry.get("task_output")
    stop = registry.get("task_stop_tool")
    monitor = registry.get("monitor_tool")
    sleep = registry.get("sleep_tool")
    assert create is not None
    assert output is not None
    assert stop is not None
    assert monitor is not None
    assert sleep is not None
    created = await create.handler({"title": "watch-me"})
    task_id = created["task"]["task_id"]
    await output.handler({"task_id": task_id, "text": "monitor line"})
    monitored = await monitor.handler({"task_id": task_id, "max_preview_chars": 64})
    assert monitored["status"] == "running"
    assert monitored["output_rows"]
    stopped = await stop.handler({"task_id": task_id, "status": "killed"})
    assert stopped["task"]["status"] == "killed"
    slept = await sleep.handler({"seconds": 0.0})
    assert slept["slept_seconds"] == 0.0
