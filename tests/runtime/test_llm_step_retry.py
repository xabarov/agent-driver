"""Provider timeout retry in LLM step."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    UsageSummary,
)
from agent_driver.runtime.single_agent.llm_step import (
    _complete_request,
    _narrow_request_tools_to_forced_choice,
)


@pytest.mark.asyncio
async def test_complete_request_retries_once_on_read_timeout() -> None:
    provider = SimpleNamespace(name="retry-test", calls=0)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        if provider.calls == 1:
            raise httpx.ReadTimeout("timed out")
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _context=SimpleNamespace(
            run_input=SimpleNamespace(stream=False, app_metadata={}),
        ),
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")])
    response = await _complete_request(host, host._context, request)
    assert response.message.content == "ok"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_complete_request_retries_invalid_encrypted_reasoning_without_echo() -> (
    None
):
    provider = SimpleNamespace(name="retry-test", calls=0)
    seen_reasoning: list[bool] = []

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        seen_reasoning.append(
            any(
                isinstance(message.metadata, dict)
                and "reasoning_details" in message.metadata
                for message in request.messages
            )
        )
        if provider.calls == 1:
            response = httpx.Response(
                400,
                text='{"error":{"code":"invalid_encrypted_content"}}',
                request=httpx.Request("POST", "https://openrouter.test/chat"),
            )
            raise httpx.HTTPStatusError(
                "bad request", request=response.request, response=response
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=False, app_metadata={}),
        metadata={},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[
            ChatMessage(
                role="assistant",
                content="",
                metadata={"reasoning_details": [{"type": "reasoning.encrypted"}]},
            )
        ]
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "ok"
    assert seen_reasoning == [True, False]
    assert (
        context.metadata["reasoning_echo_retry"] == "stripped_invalid_encrypted_content"
    )
    assert emitted[0].event_type.value == "warning"


@pytest.mark.asyncio
async def test_complete_request_retries_credit_error_with_lower_max_tokens() -> None:
    provider = SimpleNamespace(name="retry-test", calls=0)
    seen_max_tokens: list[int | None] = []

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        seen_max_tokens.append(request.max_tokens)
        if provider.calls <= 2:
            response = httpx.Response(
                402,
                text=(
                    "This request requires more credits, or fewer max_tokens. "
                    "You requested up to 4096 tokens."
                ),
                request=httpx.Request("POST", "https://openrouter.test/chat"),
            )
            raise httpx.HTTPStatusError(
                "payment required", request=response.request, response=response
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=False, app_metadata={}),
        metadata={},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")])

    response = await _complete_request(host, context, request)

    assert response.message.content == "ok"
    assert seen_max_tokens == [None, 2048, 1024]
    assert context.metadata["max_tokens_retry"] == "reduced_after_provider_402"
    assert emitted[0].event_type.value == "warning"


@pytest.mark.asyncio
async def test_complete_request_retries_forced_tool_choice_provider_error() -> None:
    provider = SimpleNamespace(name="openrouter", calls=0)
    seen_tool_choice: list[object] = []
    seen_tool_names: list[list[str]] = []

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        seen_tool_choice.append(request.tool_choice)
        seen_tool_names.append(
            [
                str(tool["function"]["name"])
                for tool in request.tools
                if isinstance(tool.get("function"), dict)
            ]
        )
        if provider.calls == 1:
            response = httpx.Response(
                400,
                text='{"error":{"message":"Provider returned error"}}',
                request=httpx.Request("POST", "https://openrouter.test/chat"),
            )
            raise httpx.HTTPStatusError(
                "bad request", request=response.request, response=response
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=False, app_metadata={}),
        metadata={},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="fetch next")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search web",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch URL",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
        tool_choice={"type": "tool", "name": "web_fetch"},
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "ok"
    assert seen_tool_choice == [{"type": "tool", "name": "web_fetch"}, None]
    assert seen_tool_names == [
        ["web_search", "web_fetch"],
        ["web_fetch"],
    ]
    assert (
        context.metadata["forced_tool_choice_retry"]
        == "removed_after_provider_rejection"
    )
    assert any(
        event.payload.get("signal_id") == "provider_forced_tool_choice_removed_retry"
        for event in emitted
    )


def test_narrow_request_tools_to_forced_choice_keeps_only_named_tool() -> None:
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="fetch next")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search web",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch URL",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
        tool_choice={"type": "tool", "name": "web_fetch"},
    )

    narrowed = _narrow_request_tools_to_forced_choice(request)

    assert [tool["function"]["name"] for tool in narrowed.tools] == ["web_fetch"]
    assert narrowed.tool_choice == {"type": "tool", "name": "web_fetch"}
    assert narrowed.metadata["forced_tool_catalog"] == "web_fetch"


@pytest.mark.asyncio
async def test_complete_request_retries_empty_forced_final_stream_without_streaming() -> (
    None
):
    provider = SimpleNamespace(name="retry-test", stream_calls=0, complete_calls=0)

    async def stream(request: LlmRequest):
        provider.stream_calls += 1
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.STOP)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.complete_calls += 1
        assert request.stream is False
        return LlmResponse(
            message=ChatMessage(role="assistant", content="final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.stream = stream
    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=True, app_metadata={}),
        metadata={"force_final_answer": True},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="answer now")],
        stream=True,
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "final answer"
    assert provider.stream_calls == 1
    assert provider.complete_calls == 1
    assert context.metadata["empty_forced_final_retry"] == "non_streaming"
    assert any(
        event.payload.get("signal_id") == "provider_empty_forced_final_non_stream_retry"
        for event in emitted
    )


@pytest.mark.asyncio
async def test_complete_request_retries_empty_forced_final_with_tools_disabled() -> (
    None
):
    provider = SimpleNamespace(name="openrouter", stream_calls=0, complete_calls=0)
    seen_tools: list[int] = []
    seen_tool_choice: list[object] = []
    seen_last_messages: list[str] = []
    seen_metadata: list[dict[str, object]] = []

    async def stream(request: LlmRequest):
        provider.stream_calls += 1
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.STOP)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.complete_calls += 1
        seen_tools.append(len(request.tools))
        seen_tool_choice.append(request.tool_choice)
        seen_last_messages.append(request.messages[-1].content)
        seen_metadata.append(dict(request.metadata))
        content = "" if provider.complete_calls == 1 else "clean final answer"
        return LlmResponse(
            message=ChatMessage(role="assistant", content=content),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.stream = stream
    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            stream=True,
            app_metadata={},
            tool_policy=SimpleNamespace(metadata={}),
        ),
        metadata={"force_final_answer": True},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="answer now")],
        stream=True,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch URL",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="none",
        model="deepseek/deepseek-v4-flash",
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "clean final answer"
    assert seen_tools == [1, 0]
    assert seen_tool_choice == ["none", None]
    assert seen_last_messages[1].startswith("Final answer retry:")
    assert seen_metadata[1]["provider_extra_body"] == {
        "reasoning": {"enabled": False, "exclude": True}
    }
    assert context.metadata["empty_forced_final_retry"] == "no_tools"
    assert any(
        event.payload.get("signal_id") == "provider_empty_forced_final_no_tools_retry"
        for event in emitted
    )
    assert any(
        event.event_type.value == "token_delta"
        and event.payload.get("delta_text") == "clean final answer"
        for event in emitted
    )
    assert any(
        event.event_type.value == "assistant_message_replaced"
        and event.payload.get("content") == "clean final answer"
        for event in emitted
    )


@pytest.mark.asyncio
async def test_complete_request_retries_tool_call_shaped_forced_final_without_tools() -> (
    None
):
    provider = SimpleNamespace(name="openrouter", complete_calls=0)
    seen_tools: list[int] = []
    seen_tool_choice: list[object] = []

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.complete_calls += 1
        seen_tools.append(len(request.tools))
        seen_tool_choice.append(request.tool_choice)
        if provider.complete_calls == 1:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content='<tool_call>{"name":"todo_write","arguments":{}}</tool_call>',
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="retry", model_name="test"),
                provider="retry",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        {
                            "tool_name": "todo_write",
                            "args": {},
                            "tool_call_id": "text_call_1",
                        }
                    ],
                    "text_form_tool_calls_parsed": True,
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="model final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            stream=False,
            app_metadata={},
            tool_policy=SimpleNamespace(metadata={}),
        ),
        metadata={"force_final_answer": True},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="answer now")],
        stream=False,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "todo_write",
                    "description": "Update todo",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="none",
        model="z-ai/glm-4.7",
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "model final answer"
    assert seen_tools == [1, 0]
    assert seen_tool_choice == ["none", None]
    assert context.metadata["forced_final_retry"] == "tool_call_no_tools"
    assert any(
        event.payload.get("signal_id")
        == "provider_forced_final_tool_call_no_tools_retry"
        for event in emitted
    )


@pytest.mark.asyncio
async def test_complete_request_retries_suppressed_text_form_forced_final_without_tools() -> (
    None
):
    provider = SimpleNamespace(name="openrouter", complete_calls=0)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.complete_calls += 1
        content = (
            "<tool_call>web_fetch<arg_key>url</arg_key>"
            "<arg_value>https://example.com</arg_value></tool_call>"
            if provider.complete_calls == 1
            else "final after suppressed tool form"
        )
        metadata = (
            {"text_form_tool_calls_suppressed": True}
            if provider.complete_calls == 1
            else {}
        )
        return LlmResponse(
            message=ChatMessage(role="assistant", content=content),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
            metadata=metadata,
        )

    provider.complete = complete
    emitted = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            stream=False,
            app_metadata={},
            tool_policy=SimpleNamespace(metadata={}),
        ),
        metadata={"force_final_answer": True},
        run_id="run_test",
        attempt_id="attempt_test",
    )
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="answer now")],
        stream=False,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch URL",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="none",
        model="z-ai/glm-4.7",
    )

    response = await _complete_request(host, context, request)

    assert response.message.content == "final after suppressed tool form"
    assert provider.complete_calls == 2
    assert context.metadata["forced_final_retry"] == "tool_call_no_tools"
