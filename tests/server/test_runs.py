"""Offline tests for the async runs API (/v1/runs)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import ToolGateAsk, ToolGateContext
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app
from agent_driver.server.runs import RunManager


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

    assert await manager.approve(record.run_id, ResumeAction.APPROVE)

    await _wait_status(record, {"completed"})
    assert "all done" in (record.answer or "")


@pytest.mark.asyncio
async def test_run_stop_while_paused() -> None:
    manager = RunManager(_gated_agent())
    record = manager.start([ChatMessage(role="user", content="run echo")])

    await _wait_status(record, {"requires_action"})
    assert manager.stop(record.run_id)

    await _wait_status(record, {"cancelled", "completed", "failed"})
    assert record.status == "cancelled"


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
