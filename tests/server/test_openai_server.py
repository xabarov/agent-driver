"""Offline tests for the OpenAI-compatible HTTP server (Starlette TestClient).

No network and no real model: ``FakeProvider`` drives the runtime and
``starlette.testclient.TestClient`` exercises the ASGI app in-process.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app


def _client(
    provider: Any = None, *, tools: Any = None, api_key: str | None = None
) -> TestClient:
    agent = create_agent(
        provider=provider or FakeProvider(response_text="Hello from agent-driver"),
        tools=tools if tools is not None else ToolSet.only(),
    )
    return TestClient(create_app(agent, model_id="agent-driver-test", api_key=api_key))


def _body(content: str, *, stream: bool = False) -> dict[str, Any]:
    return {
        "model": "agent-driver-test",
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
    }


def test_chat_completions_nonstream() -> None:
    client = _client()
    resp = client.post("/v1/chat/completions", json=_body("hi"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "agent-driver-test"
    assert data["id"].startswith("chatcmpl-")
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Hello from agent-driver"
    assert choice["finish_reason"] == "stop"
    usage = data["usage"]
    assert set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert usage["total_tokens"] >= 1


def _parse_sse(text: str) -> list[Any]:
    """Return parsed JSON payloads from an SSE body (excluding [DONE])."""
    payloads: list[Any] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        body = line[len("data: ") :]
        if body == "[DONE]":
            continue
        payloads.append(json.loads(body))
    return payloads


def test_chat_completions_stream() -> None:
    client = _client(FakeProvider(response_text="streamed answer here"))
    with client.stream(
        "POST", "/v1/chat/completions", json=_body("hi", stream=True)
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())

    assert text.rstrip().endswith("data: [DONE]")
    chunks = _parse_sse(text)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    # First chunk announces the role; last carries finish_reason.
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    # Concatenated content deltas equal the final answer.
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "streamed answer here"


class _ToolThenAnswer(FakeProvider):
    """Use a tool internally on turn 1, answer on turn 2."""

    def __init__(self) -> None:
        super().__init__(response_text="final text answer")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="tool-then-answer",
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


def test_internal_tool_use_returns_final_text() -> None:
    # The agent runs its tools internally, so the OpenAI surface returns the
    # final assistant text (finish_reason="stop"), not client-side tool_calls.
    client = _client(_ToolThenAnswer(), tools=ToolSet.only("bash"))
    resp = client.post("/v1/chat/completions", json=_body("run echo"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "final text answer"
    assert data["choices"][0]["finish_reason"] == "stop"


class _ContextSpy(FakeProvider):
    """Records the messages handed to the provider on each call."""

    def __init__(self) -> None:
        super().__init__(response_text="noted")
        self.seen: list[list[str]] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.seen.append([m.content or "" for m in request.messages])
        return await super().complete(request)


def test_session_continuity() -> None:
    spy = _ContextSpy()
    client = _client(spy)
    headers = {"X-Session-Id": "sess-abc"}

    r1 = client.post(
        "/v1/chat/completions",
        json=_body("my name is Zed"),
        headers=headers,
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/v1/chat/completions",
        json=_body("what is my name?"),
        headers=headers,
    )
    assert r2.status_code == 200

    # The second run saw the first turn (user message + assistant reply).
    second_call_context = " ".join(spy.seen[-1])
    assert "my name is Zed" in second_call_context
    assert "noted" in second_call_context


def test_session_isolation_without_header() -> None:
    spy = _ContextSpy()
    client = _client(spy)
    client.post("/v1/chat/completions", json=_body("my name is Zed"))
    client.post("/v1/chat/completions", json=_body("what is my name?"))
    # Stateless: the second run does NOT carry the first turn.
    assert "my name is Zed" not in " ".join(spy.seen[-1])


def test_auth_required() -> None:
    client = _client(api_key="secret-key")
    body = _body("hi")
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    assert (
        client.post(
            "/v1/chat/completions",
            json=body,
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
    ok = client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": "Bearer secret-key"},
    )
    assert ok.status_code == 200


def test_models_endpoint() -> None:
    client = _client()
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert data["data"][0]["id"] == "agent-driver-test"


def test_healthz() -> None:
    client = _client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("bad", ["not json at all", "{not valid}"])
def test_bad_json_body(bad: str) -> None:
    client = _client()
    resp = client.post(
        "/v1/chat/completions",
        content=bad,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
