"""Offline tests for the A2A (Agent2Agent) adapter."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent_driver.adapters.a2a import A2aServer
from agent_driver.adapters.a2a.http import create_a2a_app
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app


def _agent(answer: str = "a2a answer") -> Any:
    return create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )


def _rpc(method: str, params: dict[str, Any] | None = None, *, id: Any = 1) -> dict:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _message(text: str) -> dict[str, Any]:
    return {
        "message": {
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
            "messageId": "m1",
        }
    }


# -- core (transport-agnostic) -------------------------------------------------


@pytest.mark.asyncio
async def test_message_send_returns_completed_task() -> None:
    server = A2aServer(_agent("hello from a2a"))
    resp = await server.handle_request(_rpc("message/send", _message("hi")))
    task = resp["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    assert task["status"]["message"]["role"] == "agent"
    assert task["artifacts"][0]["parts"][0]["text"] == "hello from a2a"


@pytest.mark.asyncio
async def test_tasks_get_and_cancel() -> None:
    server = A2aServer(_agent())
    created = (await server.handle_request(_rpc("message/send", _message("hi"))))[
        "result"
    ]
    task_id = created["id"]

    got = await server.handle_request(_rpc("tasks/get", {"id": task_id}))
    assert got["result"]["id"] == task_id

    canceled = await server.handle_request(_rpc("tasks/cancel", {"id": task_id}))
    assert canceled["result"]["status"]["state"] == "canceled"

    missing = await server.handle_request(_rpc("tasks/get", {"id": "task-nope"}))
    assert missing["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_unknown_method_is_jsonrpc_error() -> None:
    server = A2aServer(_agent())
    resp = await server.handle_request(_rpc("does/not/exist"))
    assert resp["error"]["code"] == -32601


# -- HTTP transport ------------------------------------------------------------


def test_agent_card_served() -> None:
    client = TestClient(create_a2a_app(_agent(), name="agent-driver-a2a"))
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "agent-driver-a2a"
    assert card["protocolVersion"]
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "chat"


def test_http_message_send() -> None:
    client = TestClient(create_a2a_app(_agent("http a2a")))
    resp = client.post("/a2a", json=_rpc("message/send", _message("hi")))
    assert resp.status_code == 200
    task = resp.json()["result"]
    assert task["artifacts"][0]["parts"][0]["text"] == "http a2a"


def test_http_message_stream() -> None:
    client = TestClient(create_a2a_app(_agent("streamed a2a")))
    with client.stream(
        "POST", "/a2a", json=_rpc("message/stream", _message("hi"))
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    payloads = [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    states = [p["result"].get("status", {}).get("state") for p in payloads]
    assert "working" in states
    # Terminal status-update event with final=True.
    assert any(p["result"].get("kind") == "status-update" for p in payloads)
    assert any(p["result"].get("final") for p in payloads)


def test_a2a_auth_required() -> None:
    client = TestClient(create_a2a_app(_agent(), api_key="sekret"))
    # Agent card is public.
    assert client.get("/.well-known/agent-card.json").status_code == 200
    # JSON-RPC is gated.
    assert (
        client.post("/a2a", json=_rpc("message/send", _message("hi"))).status_code
        == 401
    )
    ok = client.post(
        "/a2a",
        json=_rpc("message/send", _message("hi")),
        headers={"Authorization": "Bearer sekret"},
    )
    assert ok.status_code == 200


def test_a2a_mounted_on_openai_app() -> None:
    client = TestClient(create_app(_agent(), enable_a2a=True))
    assert client.get("/healthz").status_code == 200
    assert client.get("/.well-known/agent-card.json").status_code == 200
    assert (
        client.post("/a2a", json=_rpc("message/send", _message("hi"))).status_code
        == 200
    )
