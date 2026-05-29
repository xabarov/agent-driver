"""Tests for automation adapter tools."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.automation import (
    _reset_automation_store_for_tests,
    register_automation_tools,
)
from agent_driver.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    _reset_automation_store_for_tests()


@pytest.mark.asyncio
async def test_cron_create_list_delete_flow() -> None:
    """Cron adapter tools should support deterministic create/list/delete flow."""
    registry = ToolRegistry()
    register_automation_tools(registry)
    create = registry.get("cron_create_tool")
    list_tool = registry.get("cron_list_tool")
    delete = registry.get("cron_delete_tool")
    assert create is not None
    assert list_tool is not None
    assert delete is not None
    created = await create.handler(
        {"job_name": "nightly", "schedule": "0 1 * * *", "command": "sync"}
    )
    assert created["cron_job"]["job_name"] == "nightly"
    listed = await list_tool.handler({})
    assert len(listed["cron_jobs"]) == 1
    removed = await delete.handler({"job_name": "nightly"})
    assert removed["deleted_cron_job"]["job_name"] == "nightly"


@pytest.mark.asyncio
async def test_workflow_remote_and_notification_payloads() -> None:
    """Automation adapters should return local intent envelopes."""
    registry = ToolRegistry()
    register_automation_tools(registry)
    workflow = registry.get("workflow_tool")
    remote = registry.get("remote_trigger_tool")
    pr_sub = registry.get("subscribe_pr_tool")
    push = registry.get("push_notification_tool")
    send_file = registry.get("send_user_file_tool")
    assert workflow is not None
    assert remote is not None
    assert pr_sub is not None
    assert push is not None
    assert send_file is not None
    wf = await workflow.handler({"workflow_id": "wf_a", "input": {"k": 1}})
    assert wf["workflow_event"]["workflow_id"] == "wf_a"
    assert wf["workflow_event"]["adapter_kind"] == "automation"
    rt = await remote.handler({"trigger_id": "deploy", "payload": {"env": "staging"}})
    assert rt["trigger_event"]["trigger_id"] == "deploy"
    assert rt["trigger_event"]["provenance"]["source_tool"] == "remote_trigger_tool"
    sub = await pr_sub.handler({"repo": "foo/bar", "pr_number": 7})
    assert sub["subscription"]["repo"] == "foo/bar"
    assert sub["subscription"]["subscription_id"].startswith("prsub_")
    pn = await push.handler({"title": "Done", "body": "completed"})
    assert pn["notification_event"]["title"] == "Done"
    assert pn["notification_event"]["event_id"].startswith("pn_")
    sf = await send_file.handler({"file_path": "/tmp/report.txt"})
    assert sf["file_event"]["file_path"] == "/tmp/report.txt"
    assert sf["file_event"]["adapter_kind"] == "automation"
