"""Offline normalization tests for provider payload adapters."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.base import HttpClientConfig
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest
from agent_driver.llm.providers_impl.ollama import (
    OllamaProvider,
    normalize_ollama_completion_payload,
    normalize_ollama_stream_chunk,
)
from agent_driver.llm.providers_impl.openai_compatible import (
    OpenAICompatibleProvider,
    normalize_openai_completion_payload,
    normalize_openai_stream_chunk,
)


def test_openai_completion_normalization_from_fixture() -> None:
    """Normalize OpenAI-compatible completion payload into neutral response."""
    payload = {
        "model": "gpt-test",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    response = normalize_openai_completion_payload(
        payload, provider_name="openai-compat", fallback_model="fallback"
    )
    response_payload = response.model_dump()
    assert response.message.content == "hello"
    assert response.finish_reason == LlmFinishReason.STOP
    assert response_payload["usage"]["total_tokens"] == 5
    assert "provider_usage_raw" in response_payload["metadata"]


def test_openai_completion_reads_openrouter_cost_from_usage() -> None:
    """OpenRouter-style total_cost should map to cost_usd_estimate."""
    payload = {
        "model": "qwen/test",
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "total_cost": 0.0225145,
        },
    }
    response = normalize_openai_completion_payload(
        payload,
        provider_name="openrouter",
        fallback_model="fallback",
    )
    assert response.usage is not None
    assert response.usage.cost_usd_estimate == pytest.approx(0.0225145)


def test_openai_completion_estimates_cost_from_per_1k_rate() -> None:
    payload = {
        "model": "gpt-test",
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 0, "total_tokens": 1000},
    }
    response = normalize_openai_completion_payload(
        payload,
        provider_name="openai-compat",
        fallback_model="fallback",
        cost_per_1k_tokens=0.5,
    )
    assert response.usage is not None
    assert response.usage.cost_usd_estimate == pytest.approx(0.5)


def test_openai_completion_normalizes_tool_calls_into_planned_metadata() -> None:
    """OpenAI tool_calls payload should map to planned_tool_calls metadata."""
    payload = {
        "model": "gpt-test",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"agent-driver","mock_results":[{"title":"A","url":"https://example.com"}]}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    response = normalize_openai_completion_payload(
        payload, provider_name="openai-compat", fallback_model="fallback"
    )
    planned = response.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    assert planned[0]["tool_name"] == "web_search"


def test_openai_completion_fallback_parses_text_form_tool_call() -> None:
    """Fallback parser should extract text-form tool calls from assistant content."""
    payload = {
        "model": "gpt-test",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        '<tool_call>{"name":"glob_search","arguments":'
                        '{"pattern":"README.md"}}</tool_call>'
                    ),
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    response = normalize_openai_completion_payload(
        payload, provider_name="openai-compat", fallback_model="fallback"
    )
    planned = response.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    assert planned[0]["tool_name"] == "glob_search"
    assert response.metadata.get("text_form_tool_calls_parsed") is True


def test_openai_stream_chunk_normalization_from_fixture() -> None:
    """Normalize OpenAI-compatible stream chunk into neutral stream event."""
    chunk = {
        "model": "gpt-test",
        "choices": [{"delta": {"content": "hel"}, "finish_reason": None}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openai-compat", fallback_model="fallback"
    )
    assert event.event == "delta"
    assert event.delta_text == "hel"
    assert event.finish_reason is None
    assert "provider_usage_raw" in event.metadata


def test_openai_stream_chunk_extracts_reasoning_content() -> None:
    """vLLM/Qwen3 + DeepSeek-R1 surface chain-of-thought in
    ``delta.reasoning_content`` parallel to ``delta.content``. The
    normalizer must expose it on ``LlmStreamEvent.delta_reasoning``."""
    chunk = {
        "model": "qwen3-think",
        "choices": [
            {
                "delta": {
                    "content": "answer fragment",
                    "reasoning_content": "thinking step…",
                },
                "finish_reason": None,
            }
        ],
    }
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openai-compat", fallback_model="fallback"
    )
    assert event.delta_text == "answer fragment"
    assert event.delta_reasoning == "thinking step…"


def test_openai_stream_chunk_reasoning_absent_when_no_field() -> None:
    chunk = {
        "model": "no-think",
        "choices": [{"delta": {"content": "x"}, "finish_reason": None}],
    }
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openai-compat", fallback_model="fallback"
    )
    assert event.delta_reasoning == ""


def test_openai_stream_chunk_fallback_parses_text_form_tool_call() -> None:
    """Stream chunk parser should detect text-form tool calls in delta content."""
    chunk = {
        "choices": [
            {
                "delta": {
                    "content": (
                        '<|python_tag|>{"name":"web_search","parameters":'
                        '{"query":"agent-driver"}}<|eom_id|>'
                    )
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openai-compat", fallback_model="fallback"
    )
    planned = event.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    assert planned[0]["tool_name"] == "web_search"
    assert event.metadata.get("text_form_tool_calls_parsed") is True


def test_openai_usage_metadata_includes_cached_tokens_when_present() -> None:
    """Cached token details should be normalized into metadata when provided."""
    payload = {
        "model": "gpt-test",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 2},
        },
    }
    response = normalize_openai_completion_payload(
        payload, provider_name="openai-compat", fallback_model="fallback"
    )
    response_payload = response.model_dump()
    assert response_payload["metadata"]["cached_input_tokens"] == 2


def test_ollama_completion_normalization_from_fixture() -> None:
    """Normalize Ollama completion payload into neutral response."""
    payload = {
        "model": "llama3:8b",
        "message": {"content": "done"},
        "prompt_eval_count": 4,
        "eval_count": 6,
    }
    response = normalize_ollama_completion_payload(
        payload, provider_name="ollama", fallback_model="fallback"
    )
    response_payload = response.model_dump()
    assert response.message.content == "done"
    assert response_payload["usage"]["total_tokens"] == 10
    assert "provider_usage_raw" in response_payload["metadata"]


def test_ollama_stream_chunk_normalization_from_fixture() -> None:
    """Normalize Ollama stream done chunk and usage metadata."""
    chunk = {
        "message": {"content": ""},
        "done": True,
        "prompt_eval_count": 2,
        "eval_count": 3,
    }
    event = normalize_ollama_stream_chunk(
        chunk, provider_name="ollama", fallback_model="llama3:8b"
    )
    event_payload = event.model_dump()
    assert event.event == "done"
    assert event.finish_reason == LlmFinishReason.STOP
    assert event.usage is not None
    assert event_payload["usage"]["total_tokens"] == 5
    assert "provider_usage_raw" in event_payload["metadata"]


def test_provider_config_constructors() -> None:
    """Provider config wrappers should initialize adapters correctly."""
    openai_provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai",
            base_url="https://example.local/v1",
            api_key="token",
            model="gpt-test",
        )
    )
    ollama_provider = OllamaProvider()

    assert openai_provider.name == "openai"
    assert ollama_provider.name == "ollama"


@pytest.mark.asyncio
async def test_openai_stream_adapter_uses_mock_transport_progressively() -> None:
    """OpenAI provider stream should emit progressive events from mocked transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = "\n".join(
                [
                    'data: {"choices":[{"delta":{"content":"hel"},"finish_reason":null}]}',
                    (
                        'data: {"choices":[{"delta":{"content":"lo"},'
                        '"finish_reason":"stop"}], "usage":{"prompt_tokens":1,'
                        '"completion_tokens":1,"total_tokens":2}}'
                    ),
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=transport),
        )
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
    events = [event async for event in provider.stream(request)]
    assert [event.delta_text for event in events] == ["hel", "lo"]


@pytest.mark.asyncio
async def test_openai_stream_adapter_ignores_empty_choices_chunk() -> None:
    """OpenRouter can emit bookkeeping chunks with choices=[] during streaming."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = "\n".join(
                [
                    'data: {"choices":[]}',
                    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
    events = [event async for event in provider.stream(request)]
    assert [event.delta_text for event in events] == ["", "ok"]


@pytest.mark.asyncio
async def test_openai_stream_adapter_parses_split_text_form_tool_call() -> None:
    """Fallback parser should see text-form tool calls split across chunks."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            first = {
                "choices": [
                    {
                        "delta": {
                            "content": (
                                '<tool_call>{"name":"web_search","arguments":'
                                '{"query":"Fen'
                            )
                        },
                        "finish_reason": None,
                    }
                ]
            }
            second = {
                "choices": [
                    {
                        "delta": {"content": 'der","max_results":5}}</tool_call>'},
                        "finish_reason": "stop",
                    }
                ]
            }
            body = "\n".join(
                [
                    f"data: {json.dumps(first)}",
                    f"data: {json.dumps(second)}",
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )

    events = [
        event
        async for event in provider.stream(
            LlmRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
        )
    ]

    assert [event.delta_text for event in events[:2]] == [
        '<tool_call>{"name":"web_search","arguments":{"query":"Fen',
        'der","max_results":5}}</tool_call>',
    ]
    planned = events[-1].metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    assert planned[0]["tool_name"] == "web_search"
    assert planned[0]["args"] == {"query": "Fender", "max_results": 5}
    assert events[-1].metadata.get("text_form_tool_calls_parsed") is True


@pytest.mark.asyncio
async def test_openai_stream_adapter_recovers_forced_tool_args_as_text() -> None:
    """Some OpenAI-compatible models stream only args when tool_choice is forced."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = "\n".join(
                [
                    'data: {"choices":[{"delta":{"content":"0.5, \\"query"},"finish_reason":null}]}',
                    (
                        'data: {"choices":[{"delta":{"content":"\\": \\"история Fender\\"}'
                        '\\n</tool_call>"},"finish_reason":"stop"}]}'
                    ),
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )

    events = [
        event
        async for event in provider.stream(
            LlmRequest(
                messages=[ChatMessage(role="user", content="hi")],
                stream=True,
                tool_choice={"type": "tool", "name": "web_search"},
            )
        )
    ]

    planned = events[-1].metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    assert planned[0]["tool_name"] == "web_search"
    assert planned[0]["args"] == {"query": "история Fender"}
    assert events[-1].metadata.get("text_form_tool_calls_parsed") is True


@pytest.mark.asyncio
async def test_openai_stream_adapter_suppresses_text_form_tools_when_disabled() -> None:
    """tool_choice='none' should prevent text-form tool-call execution loops."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = "\n".join(
                [
                    (
                        'data: {"choices":[{"delta":{"content":"<tool_call>{'
                        '\\"name\\":\\"web_search\\",\\"arguments\\":{'
                        '\\"query\\":\\"Fender\\"}}</tool_call>"},"finish_reason":"stop"}]}'
                    ),
                    "data: [DONE]",
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )

    events = [
        event
        async for event in provider.stream(
            LlmRequest(
                messages=[ChatMessage(role="user", content="hi")],
                stream=True,
                tool_choice="none",
            )
        )
    ]

    assert events[0].metadata.get("planned_tool_calls") is None
    assert events[0].metadata.get("text_form_tool_calls_suppressed") is True


@pytest.mark.asyncio
async def test_openai_complete_sends_tools_when_present() -> None:
    """OpenAI complete payload should include tools/tool_choice from request."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "model": "gpt-test",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )
    _ = await provider.complete(
        LlmRequest(
            messages=[ChatMessage(role="user", content="hi")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    )
    body = str(captured.get("body") or "")
    assert '"tools"' in body
    assert '"tool_choice"' in body


@pytest.mark.asyncio
async def test_openai_complete_passes_explicit_tool_choice_and_tool_messages() -> None:
    """Provider payload should preserve tool_choice and tool protocol messages."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "model": "gpt-test",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        return httpx.Response(404, json={})

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )
    _ = await provider.complete(
        LlmRequest(
            messages=[
                ChatMessage(
                    role="assistant",
                    content="",
                    metadata={
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": "{}"},
                            }
                        ]
                    },
                ),
                ChatMessage(
                    role="tool",
                    name="web_search",
                    tool_call_id="call_1",
                    content='{"summary":"ok"}',
                ),
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice="none",
        )
    )
    payload = json.loads(str(captured.get("body") or "{}"))
    assert payload.get("tool_choice") == "none"
    assert isinstance(payload.get("messages"), list)
    assert payload["messages"][0].get("tool_calls")
    assert payload["messages"][1].get("tool_call_id") == "call_1"


@pytest.mark.asyncio
async def test_openai_stream_adapter_marks_failure_on_stream_error() -> None:
    """OpenAI provider stream should update telemetry on stream failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        raise httpx.ReadTimeout("stream failed")

    transport = httpx.MockTransport(handler)
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key=None,
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=transport),
        )
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
    with pytest.raises(httpx.ReadTimeout):
        _ = [event async for event in provider.stream(request)]
    assert provider.status.request_count == 1
    assert provider.status.error_count == 1
    assert provider.status.healthy is False


@pytest.mark.asyncio
async def test_ollama_stream_adapter_uses_mock_transport_progressively() -> None:
    """Ollama provider stream should emit progressive events from mocked transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/chat"):
            body = "\n".join(
                [
                    '{"message":{"content":"he"},"done":false}',
                    (
                        '{"message":{"content":"llo"},"done":true,'
                        '"prompt_eval_count":1,"eval_count":2}'
                    ),
                ]
            )
            return httpx.Response(200, text=body)
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    provider = OllamaProvider(
        config=OllamaProvider.Config(
            base_url="https://mock.local",
            model="llama3:8b",
            http_client_config=HttpClientConfig(transport=transport),
        )
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], stream=True)
    events = [event async for event in provider.stream(request)]
    assert [event.delta_text for event in events] == ["he", "llo"]
    assert events[-1].event == "done"
