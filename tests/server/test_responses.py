"""Offline tests for the OpenAI Responses API (/v1/responses)."""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app


def _client(provider: Any = None) -> TestClient:
    agent = create_agent(
        provider=provider or FakeProvider(response_text="response answer"),
        tools=ToolSet.only(),
    )
    return TestClient(create_app(agent, model_id="agent-driver-test"))


def test_responses_nonstream_shape() -> None:
    client = _client()
    resp = client.post("/v1/responses", json={"model": "m", "input": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "response"
    assert data["id"].startswith("resp_")
    assert data["status"] == "completed"
    assert data["output_text"] == "response answer"
    assert data["output"][0]["content"][0]["text"] == "response answer"
    assert set(data["usage"]) == {"input_tokens", "output_tokens", "total_tokens"}


def test_responses_input_as_messages_and_instructions() -> None:
    class Spy(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="ok")
            self.seen: list[tuple[str, str]] = []

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.seen = [
                (str(getattr(m.role, "value", m.role)), m.content or "")
                for m in request.messages
            ]
            return await super().complete(request)

    spy = Spy()
    client = _client(spy)
    resp = client.post(
        "/v1/responses",
        json={
            "model": "m",
            "instructions": "You are terse.",
            "input": [{"role": "user", "content": "hi there"}],
        },
    )
    assert resp.status_code == 200
    # The system instructions + user input reach the model.
    joined = " | ".join(f"{r}:{c}" for r, c in spy.seen)
    assert "system:You are terse." in joined
    assert "user:hi there" in joined


def test_responses_get_and_delete_roundtrip() -> None:
    client = _client()
    created = client.post("/v1/responses", json={"model": "m", "input": "hi"}).json()
    rid = created["id"]

    got = client.get(f"/v1/responses/{rid}")
    assert got.status_code == 200
    assert got.json()["id"] == rid

    deleted = client.request("DELETE", f"/v1/responses/{rid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    assert client.get(f"/v1/responses/{rid}").status_code == 404


def test_responses_not_stored_when_store_false() -> None:
    client = _client()
    created = client.post(
        "/v1/responses", json={"model": "m", "input": "hi", "store": False}
    ).json()
    assert client.get(f"/v1/responses/{created['id']}").status_code == 404


def test_responses_chaining_previous_response_id() -> None:
    class Spy(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="noted")
            self.last: list[str] = []

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.last = [m.content or "" for m in request.messages]
            return await super().complete(request)

    spy = Spy()
    client = _client(spy)
    first = client.post(
        "/v1/responses", json={"model": "m", "input": "my name is Zed"}
    ).json()

    client.post(
        "/v1/responses",
        json={
            "model": "m",
            "input": "what is my name?",
            "previous_response_id": first["id"],
        },
    )
    context = " ".join(spy.last)
    # The chained turn carried the prior user turn + assistant answer.
    assert "my name is Zed" in context
    assert "noted" in context
    assert "what is my name?" in context


def test_responses_stream_events() -> None:
    client = _client(FakeProvider(response_text="streamed response"))
    with client.stream(
        "POST", "/v1/responses", json={"model": "m", "input": "hi", "stream": True}
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event: response.created" in text
    assert "event: response.output_text.delta" in text
    assert "event: response.completed" in text
    # Concatenated deltas equal the answer.
    deltas = [
        json.loads(line[len("data: ") :])["delta"]
        for line in text.splitlines()
        if line.startswith("data: ") and '"delta"' in line
    ]
    assert "".join(deltas) == "streamed response"


def test_responses_auth_required() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    client = TestClient(create_app(agent, api_key="sekret"))
    assert (
        client.post("/v1/responses", json={"model": "m", "input": "hi"}).status_code
        == 401
    )
    ok = client.post(
        "/v1/responses",
        json={"model": "m", "input": "hi"},
        headers={"Authorization": "Bearer sekret"},
    )
    assert ok.status_code == 200
