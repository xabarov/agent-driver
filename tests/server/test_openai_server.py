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


def test_output_audio_surfaces_on_message() -> None:
    # A model that returns an assistant ``audio`` object (modalities=["text",
    # "audio"]) has it carried through the run onto the completion message.
    audio = {"id": "audio_1", "data": "UklGRiQ=", "transcript": "hi", "format": "wav"}
    provider = FakeProvider(
        response_text="hi", response_message_metadata={"output_audio": audio}
    )
    client = _client(provider)
    resp = client.post("/v1/chat/completions", json=_body("say hi"))
    assert resp.status_code == 200
    message = resp.json()["choices"][0]["message"]
    assert message["audio"] == audio


class _AudioStreamProvider(FakeProvider):
    """Streams two ``delta.audio`` segments then finishes (no text deltas)."""

    def __init__(self) -> None:
        super().__init__(response_text="")

    async def stream(self, request: LlmRequest):
        from agent_driver.contracts.usage import UsageSummary
        from agent_driver.llm.contracts import LlmStreamEvent

        yield LlmStreamEvent(
            event="delta",
            metadata={"output_audio_delta": {"id": "a1", "data": "aGVs", "transcript": "He"}},
        )
        yield LlmStreamEvent(
            event="delta",
            metadata={"output_audio_delta": {"data": "bG8=", "transcript": "llo", "format": "pcm16"}},
        )
        yield LlmStreamEvent(
            event="delta",
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(
                input_tokens=1, output_tokens=1, total_tokens=2,
                model_provider="fake", model_name="gpt-audio",
            ),
        )


def test_streaming_output_audio_emitted_as_chunk() -> None:
    client = _client(_AudioStreamProvider())
    body = _body("say hi", stream=True)
    body["modalities"] = ["text", "audio"]
    body["audio"] = {"voice": "alloy", "format": "pcm16"}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    chunks = _parse_sse(resp.text)
    audio_deltas = [
        c["choices"][0]["delta"]["audio"]
        for c in chunks
        if c.get("choices") and "audio" in c["choices"][0].get("delta", {})
    ]
    assert len(audio_deltas) == 1
    audio = audio_deltas[0]
    assert audio["transcript"] == "Hello"
    import base64

    assert base64.b64decode(audio["data"]) == b"hello"


class _ExtraBodySpy(FakeProvider):
    """Records the provider_extra_body handed to the provider per call."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.extra_body: list[Any] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.extra_body.append(request.metadata.get("provider_extra_body"))
        return await super().complete(request)


def test_output_media_request_params_reach_provider() -> None:
    # modalities/audio on the request are forwarded to the provider request as
    # provider_extra_body (which the payload builder emits as top-level params).
    spy = _ExtraBodySpy()
    client = _client(spy)
    body = _body("say hi")
    body["modalities"] = ["text", "audio"]
    body["audio"] = {"voice": "alloy", "format": "wav"}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    assert spy.extra_body[-1] == {
        "modalities": ["text", "audio"],
        "audio": {"voice": "alloy", "format": "wav"},
    }


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


# -- production-readiness ------------------------------------------------------


class _Boom(FakeProvider):
    """Provider that always raises, to exercise error mapping."""

    async def complete(self, request: LlmRequest) -> LlmResponse:
        raise RuntimeError("provider exploded")


def test_provider_error_is_openai_envelope() -> None:
    agent = create_agent(provider=_Boom(), tools=ToolSet.only())
    client = TestClient(create_app(agent), raise_server_exceptions=False)
    resp = client.post("/v1/chat/completions", json=_body("hi"))
    assert resp.status_code in (500, 502, 504)
    assert resp.headers["content-type"].startswith("application/json")
    err = resp.json()["error"]
    assert err["type"] and "message" in err


def test_failed_run_maps_to_error_unit() -> None:
    # Unit-level: a non-completed terminal run becomes an OpenAI error body.
    # status_and_payload_for_output reads only status/terminal_reason/answer,
    # so a duck-typed stub avoids AgentRunOutput's terminal-event validator.
    from types import SimpleNamespace

    from agent_driver.contracts.enums import RunStatus, TerminalReason
    from agent_driver.server import errors

    failed = SimpleNamespace(
        status=RunStatus.FAILED,
        terminal_reason=TerminalReason.APPROVAL_REJECTED,
        answer=None,
    )
    mapped = errors.status_and_payload_for_output(failed)
    assert mapped is not None
    status, payload = mapped
    assert status == 500
    assert payload["error"]["type"] == "run_failed"

    completed = SimpleNamespace(
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        answer="ok",
    )
    assert errors.status_and_payload_for_output(completed) is None


def test_stream_include_usage_emits_usage_chunk() -> None:
    client = _client(FakeProvider(response_text="hello world"))
    body = _body("hi", stream=True)
    body["stream_options"] = {"include_usage": True}
    with client.stream("POST", "/v1/chat/completions", json=body) as resp:
        text = "".join(resp.iter_text())
    chunks = _parse_sse(text)
    usage_chunks = [c for c in chunks if c.get("usage")]
    assert len(usage_chunks) == 1
    # Usage chunk carries an empty choices array per the OpenAI contract.
    assert usage_chunks[0]["choices"] == []
    assert usage_chunks[0]["usage"]["total_tokens"] >= 1


def test_stream_omits_usage_chunk_by_default() -> None:
    client = _client(FakeProvider(response_text="hello"))
    with client.stream(
        "POST", "/v1/chat/completions", json=_body("hi", stream=True)
    ) as resp:
        text = "".join(resp.iter_text())
    assert all(not c.get("usage") for c in _parse_sse(text))


class _RequestSpy(FakeProvider):
    """Captures the LlmRequest fields the runtime builds."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.last: LlmRequest | None = None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.last = request
        return await super().complete(request)


def test_sampling_and_response_format_passthrough() -> None:
    spy = _RequestSpy()
    client = _client(spy)
    body = _body("hi")
    body["temperature"] = 0.3
    body["max_tokens"] = 256
    body["response_format"] = {"type": "json_object"}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    assert spy.last is not None
    assert spy.last.temperature == 0.3
    assert spy.last.max_tokens == 256
    assert spy.last.response_format == {"type": "json_object"}


def test_session_lru_eviction() -> None:
    spy = _ContextSpy()
    agent = create_agent(provider=spy, tools=ToolSet.only())
    client = TestClient(create_app(agent, max_sessions=2))

    for sid in ("s1", "s2", "s3"):  # 3 sessions, cap 2 -> s1 evicted
        client.post(
            "/v1/chat/completions",
            json=_body("remember me"),
            headers={"X-Session-Id": sid},
        )
    # s1 was evicted: a follow-up on s1 does NOT see its earlier turn.
    spy.seen.clear()
    client.post(
        "/v1/chat/completions",
        json=_body("what did I say?"),
        headers={"X-Session-Id": "s1"},
    )
    assert "remember me" not in " ".join(spy.seen[-1])


# -- browser-client compatibility ---------------------------------------------


def test_security_headers_present() -> None:
    client = _client()
    resp = client.post("/v1/chat/completions", json=_body("hi"))
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_cors_preflight_and_headers() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    client = TestClient(create_app(agent, cors_origins=["https://chat.example"]))
    # Preflight.
    pre = client.options(
        "/v1/chat/completions",
        headers={
            "Origin": "https://chat.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert pre.status_code in (200, 204)
    assert pre.headers["access-control-allow-origin"] == "https://chat.example"
    # Actual request echoes the allowed origin.
    resp = client.post(
        "/v1/chat/completions",
        json=_body("hi"),
        headers={"Origin": "https://chat.example"},
    )
    assert resp.headers["access-control-allow-origin"] == "https://chat.example"


def test_cors_absent_by_default() -> None:
    client = _client()
    resp = client.post(
        "/v1/chat/completions",
        json=_body("hi"),
        headers={"Origin": "https://chat.example"},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_multimodal_content_flattened() -> None:
    spy = _ContextSpy()
    client = _client(spy)
    body = {
        "model": "agent-driver-test",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe "},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,Zm9v"},
                    },
                    {"type": "text", "text": "this image"},
                ],
            }
        ],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    # Text parts concatenated; the image part is skipped, not fatal.
    assert "describe this image" in " ".join(spy.seen[-1])


def test_content_flatten_shapes() -> None:
    from agent_driver.server.openai.schema import ChatMessageIn

    assert ChatMessageIn(role="user", content="plain").text_content() == "plain"
    assert ChatMessageIn(role="user", content=None).text_content() == ""
    assert ChatMessageIn(role="user", content={"text": "d"}).text_content() == "d"
    assert (
        ChatMessageIn(
            role="user", content=["a", {"type": "text", "text": "b"}]
        ).text_content()
        == "ab"
    )


def test_image_url_parts_reach_provider_as_attachments() -> None:
    class _Spy(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="a cat")
            self.last: Any = None

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.last = request
            return await super().complete(request)

    spy = _Spy()
    client = _client(spy)
    body = {
        "model": "agent-driver-test",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://x/cat.png"}},
                ],
            }
        ],
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    user = [m for m in spy.last.messages if str(m.role.value) == "user"][-1]
    assert user.content == "what is in this image?"
    assert user.metadata.get("attachments") == [
        {"kind": "image", "url": "https://x/cat.png"}
    ]
