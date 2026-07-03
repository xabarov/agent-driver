"""Offline tests for the async runs API (/v1/runs)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.durable_lifecycle import (
    DurableApprovalStatus,
    DurableInterruptStatus,
    DurableLifecycleStatus,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import ToolGateAsk, ToolGateContext
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app
from agent_driver.server.runs import (
    RunManager,
    RunRecord,
    harness_adapter_events_for_server_run,
    server_harness_adapter_capability,
)
from agent_driver.harness import DurableLifecycleRepository


def _body(content: str) -> dict[str, Any]:
    return {"model": "agent-driver", "messages": [{"role": "user", "content": content}]}


def _poll(
    client: TestClient, run_id: str, *, until: set[str], tries: int = 100
) -> dict:
    for _ in range(tries):
        resp = client.get(f"/v1/runs/{run_id}")
        data = resp.json()
        if data["status"] in until:
            return data
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {until}: last={data}")


def test_run_completes() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="async answer"), tools=ToolSet.only()
    )
    client = TestClient(create_app(agent))

    start = client.post("/v1/runs", json=_body("hi"))
    assert start.status_code == 202
    run_id = start.json()["id"]
    assert start.json()["status"] in ("queued", "running")

    done = _poll(client, run_id, until={"completed"})
    assert done["answer"] == "async answer"
    assert done["usage"]["total_tokens"] >= 1
    assert done["lifecycle"]["state"] == "completed"
    assert done["lifecycle"]["terminal_event"] == "run_completed"
    assert done["lifecycle"]["reconnect_cursor"].startswith(f"{run_id}:")


def test_run_events_stream() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="streamed run"), tools=ToolSet.only()
    )
    client = TestClient(create_app(agent))
    run_id = client.post("/v1/runs", json=_body("hi")).json()["id"]

    with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "event: run.started" in text
    assert "event: run.completed" in text
    assert text.rstrip().endswith("data: [DONE]")


async def _ask_gate(_ctx: ToolGateContext) -> ToolGateAsk:
    return ToolGateAsk(message="approve?")


class _BashThenFinish(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="all done")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="bash-then-finish",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash", args={"command": "echo hi"}
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


# The paused approve/stop lifecycle is exercised at the RunManager level: the
# Starlette TestClient runs the app loop only during a request, which starves a
# background task that parks on an approval future, so HTTP polling can't drive
# it deterministically. The endpoint wiring (approval -> 200/409, stop) is
# covered by the HTTP tests; the lifecycle itself is covered here.
def _gated_agent() -> Any:
    return create_agent(
        provider=_BashThenFinish(), tools=ToolSet.only("bash"), tool_gate=_ask_gate
    )


async def _wait_status(record: Any, until: set[str], tries: int = 200) -> None:
    for _ in range(tries):
        if record.status in until:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"did not reach {until}: status={record.status}")


@pytest.mark.asyncio
async def test_run_requires_action_then_approve() -> None:
    manager = RunManager(_gated_agent())
    record = manager.start([ChatMessage(role="user", content="run echo")])

    await _wait_status(record, {"requires_action"})
    assert record.interrupt and record.interrupt["interrupt_id"]
    paused_lifecycle = record.public()["lifecycle"]
    assert paused_lifecycle["state"] == "awaiting_input"
    assert paused_lifecycle["resume_available"] is True

    assert await manager.approve(record.run_id, ResumeAction.APPROVE)

    await _wait_status(record, {"completed"})
    assert "all done" in (record.answer or "")
    assert record.public()["lifecycle"]["state"] == "completed"


@pytest.mark.asyncio
async def test_run_manager_optional_durable_lifecycle_writer_records_approval() -> None:
    repository = DurableLifecycleRepository()
    manager = RunManager(_gated_agent(), durable_lifecycle_writer=repository)
    record = manager.start([ChatMessage(role="user", content="run echo")])

    await _wait_status(record, {"requires_action"})
    run = repository.get_run(record.run_id)
    assert run is not None
    assert run.status == DurableLifecycleStatus.PAUSED
    assert run.durability_level.value == "process_local"
    assert repository.attach_plan(record.run_id).verdict.value == "attach_live"
    interrupt = next(iter(repository.interrupts.values()))
    approval = next(iter(repository.approvals.values()))
    assert interrupt.status == DurableInterruptStatus.PENDING
    assert approval.status == DurableApprovalStatus.PENDING

    assert await manager.approve(record.run_id, ResumeAction.APPROVE)
    await _wait_status(record, {"completed"})

    resolved_interrupt = repository.interrupts[interrupt.interrupt_id]
    resolved_approval = repository.approvals[approval.approval_id]
    assert resolved_interrupt.status == DurableInterruptStatus.RESOLVED
    assert resolved_approval.status == DurableApprovalStatus.APPROVED
    assert repository.get_run(record.run_id).status == DurableLifecycleStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_stop_while_paused() -> None:
    manager = RunManager(_gated_agent())
    record = manager.start([ChatMessage(role="user", content="run echo")])

    await _wait_status(record, {"requires_action"})
    assert manager.stop(record.run_id)
    assert record.public()["lifecycle"]["state"] == "cancelling"

    await _wait_status(record, {"cancelled", "completed", "failed"})
    assert record.status == "cancelled"
    assert record.public()["lifecycle"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_run_manager_optional_durable_lifecycle_writer_records_stop() -> None:
    repository = DurableLifecycleRepository()
    manager = RunManager(_gated_agent(), durable_lifecycle_writer=repository)
    record = manager.start([ChatMessage(role="user", content="run echo")])

    await _wait_status(record, {"requires_action"})
    assert manager.stop(record.run_id)
    await _wait_status(record, {"cancelled", "completed", "failed"})

    run = repository.get_run(record.run_id)
    assert run is not None
    assert run.abort_request_id == f"{record.run_id}:runs_stop"
    assert f"{record.run_id}:runs_stop" in repository.aborts


def test_get_unknown_run_404() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    client = TestClient(create_app(agent))
    assert client.get("/v1/runs/run_nope").status_code == 404


def test_approval_conflict_when_not_paused() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="done"), tools=ToolSet.only()
    )
    client = TestClient(create_app(agent))
    run_id = client.post("/v1/runs", json=_body("hi")).json()["id"]
    _poll(client, run_id, until={"completed"})
    # Not awaiting approval -> 409.
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"action": "approve"})
    assert resp.status_code == 409


def test_runs_auth_required() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    client = TestClient(create_app(agent, api_key="sekret"))
    assert client.post("/v1/runs", json=_body("hi")).status_code == 401
    ok = client.post(
        "/v1/runs", json=_body("hi"), headers={"Authorization": "Bearer sekret"}
    )
    assert ok.status_code == 202


def test_server_shared_harness_adapter_projection_redacts_and_declares_capability() -> (
    None
):
    record = RunRecord(
        run_id="run_server",
        created=123,
        thread_id="session_server",
    )
    record.events = [
        {
            "event": "run.started",
            "seq": 1,
            "data": {"run_id": "run_server"},
        },
        {
            "event": "run.requires_action",
            "seq": 2,
            "data": {
                "run_id": "run_server",
                "interrupt_id": "approval_server",
                "tool_name": "bash",
                "allowed_actions": ["approve", "reject"],
                "api_key": "sk-should-not-leak",
            },
        },
    ]

    rows = harness_adapter_events_for_server_run(record)

    assert [row.cursor for row in rows] == ["run_server:1", "run_server:2"]
    assert rows[1].approval_request is not None
    assert rows[1].approval_request.request_id == "approval_server"
    assert rows[1].redacted_metadata["app_metadata"] == {}
    capability = server_harness_adapter_capability()
    assert capability.protocol == "openai_compatible_http"
    assert capability.features["approvals"] == "supported"
    assert capability.features["live_gates"] == "no_claim"
