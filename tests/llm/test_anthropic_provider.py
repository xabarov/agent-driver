"""Offline tests for the Anthropic provider adapter (P3a H6).

Uses httpx.MockTransport so no live API call is made — exercises payload
shaping, header construction, response normalization, and SSE stream
event mapping in full.
"""

from __future__ import annotations

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.base import HttpClientConfig
from agent_driver.llm.contracts import LlmFinishReason, LlmProviderKind, LlmRequest
from agent_driver.llm.providers_impl.anthropic import (
    AnthropicProvider,
    _map_stop_reason,
    _normalize_messages,
    _parse_sse_event_line,
    _stream_event_from_anthropic,
    normalize_anthropic_completion_payload,
)


# ---------------------------------------------------------------------------
# Pure normalization helpers
# ---------------------------------------------------------------------------


class TestStopReasonMapping:
    def test_end_turn_maps_to_stop(self) -> None:
        assert _map_stop_reason("end_turn") is LlmFinishReason.STOP

    def test_max_tokens_maps_to_length(self) -> None:
        assert _map_stop_reason("max_tokens") is LlmFinishReason.LENGTH

    def test_tool_use_maps_to_tool_calls(self) -> None:
        assert _map_stop_reason("tool_use") is LlmFinishReason.TOOL_CALLS

    def test_stop_sequence_maps_to_stop(self) -> None:
        assert _map_stop_reason("stop_sequence") is LlmFinishReason.STOP

    def test_unknown_returns_unknown(self) -> None:
        assert _map_stop_reason("alien_reason") is LlmFinishReason.UNKNOWN

    def test_none_returns_unknown(self) -> None:
        assert _map_stop_reason(None) is LlmFinishReason.UNKNOWN


class TestMessageNormalization:
    def test_extracts_system_messages_separately(self) -> None:
        system_text, messages = _normalize_messages(
            [
                ChatMessage(role="system", content="You are helpful."),
                ChatMessage(role="user", content="Hello"),
            ]
        )
        assert system_text == "You are helpful."
        assert messages == [{"role": "user", "content": "Hello"}]

    def test_merges_consecutive_same_role_messages(self) -> None:
        _, messages = _normalize_messages(
            [
                ChatMessage(role="user", content="part one"),
                ChatMessage(role="user", content="part two"),
            ]
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        # Merged into content array
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["text"] == "part one"
        assert content[1]["text"] == "part two"

    def test_alternating_messages_preserved(self) -> None:
        _, messages = _normalize_messages(
            [
                ChatMessage(role="user", content="q1"),
                ChatMessage(role="assistant", content="a1"),
                ChatMessage(role="user", content="q2"),
            ]
        )
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]

    def test_combines_multiple_system_messages(self) -> None:
        system_text, _ = _normalize_messages(
            [
                ChatMessage(role="system", content="line one"),
                ChatMessage(role="system", content="line two"),
            ]
        )
        assert "line one" in system_text and "line two" in system_text


class TestCompletionNormalization:
    def test_extracts_text_from_content_blocks(self) -> None:
        payload = {
            "model": "claude-3-5-haiku-latest",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello there"}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        resp = normalize_anthropic_completion_payload(
            payload, provider_name="anthropic", fallback_model="claude-3-5-haiku-latest"
        )
        assert resp.message.content == "Hello there"
        assert resp.finish_reason is LlmFinishReason.STOP
        assert resp.usage.input_tokens == 5
        assert resp.usage.output_tokens == 3
        assert resp.usage.total_tokens == 8
        assert resp.model == "claude-3-5-haiku-latest"

    def test_concatenates_multiple_text_blocks(self) -> None:
        payload = {
            "content": [
                {"type": "text", "text": "part1 "},
                {"type": "text", "text": "part2"},
            ],
            "stop_reason": "end_turn",
        }
        resp = normalize_anthropic_completion_payload(
            payload, provider_name="anthropic", fallback_model="x"
        )
        assert resp.message.content == "part1 part2"

    def test_tool_use_finish_reason(self) -> None:
        payload = {
            "stop_reason": "tool_use",
            "content": [{"type": "text", "text": "calling tool"}],
        }
        resp = normalize_anthropic_completion_payload(
            payload, provider_name="anthropic", fallback_model="x"
        )
        assert resp.finish_reason is LlmFinishReason.TOOL_CALLS


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


class TestSseParsing:
    def test_parses_event_with_data_line(self) -> None:
        out = _parse_sse_event_line(["event: message_start", "data: {\"type\":\"message_start\"}"])
        assert out is not None
        assert out[0] == "message_start"
        assert out[1]["type"] == "message_start"

    def test_returns_none_when_no_data(self) -> None:
        assert _parse_sse_event_line(["event: ping"]) is None

    def test_returns_none_when_data_not_json(self) -> None:
        assert _parse_sse_event_line(["event: x", "data: not-json"]) is None


class TestStreamEventMapping:
    def test_content_block_delta_text_yields_delta(self) -> None:
        ev = _stream_event_from_anthropic(
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": "hi"}},
            provider_name="anthropic",
            model_name="claude",
        )
        assert ev is not None
        assert ev.event == "delta"
        assert ev.delta_text == "hi"

    def test_message_delta_with_stop_reason_yields_done(self) -> None:
        ev = _stream_event_from_anthropic(
            "message_delta",
            {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
            provider_name="anthropic",
            model_name="claude",
        )
        assert ev is not None
        assert ev.event == "done"
        assert ev.finish_reason is LlmFinishReason.STOP
        assert ev.usage is not None
        assert ev.usage.output_tokens == 5

    def test_message_stop_yields_done(self) -> None:
        ev = _stream_event_from_anthropic(
            "message_stop", {}, provider_name="anthropic", model_name="claude"
        )
        assert ev is not None
        assert ev.event == "done"

    def test_unknown_event_yields_none(self) -> None:
        assert (
            _stream_event_from_anthropic(
                "ping", {}, provider_name="anthropic", model_name="claude"
            )
            is None
        )


# ---------------------------------------------------------------------------
# Provider adapter — payload shaping + mock-transport round-trip
# ---------------------------------------------------------------------------


class TestProviderAdapter:
    def test_kind_is_anthropic_in_status(self) -> None:
        provider = AnthropicProvider(
            config=AnthropicProvider.Config(api_key="sk-test", model="claude-3-5")
        )
        assert provider.status.provider_kind is LlmProviderKind.ANTHROPIC

    def test_configured_flag_reflects_api_key_presence(self) -> None:
        configured = AnthropicProvider(config=AnthropicProvider.Config(api_key="sk-x"))
        not_configured = AnthropicProvider(config=AnthropicProvider.Config(api_key=""))
        assert configured.status.configured is True
        assert not_configured.status.configured is False

    @pytest.mark.asyncio
    async def test_complete_sends_required_headers_and_payload(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "hello back"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
            )

        provider = AnthropicProvider(
            config=AnthropicProvider.Config(
                name="anthropic-mock",
                base_url="https://mock.local",
                api_key="sk-mock",
                model="claude-test",
                http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
            )
        )
        request = LlmRequest(
            messages=[
                ChatMessage(role="system", content="You are X."),
                ChatMessage(role="user", content="hi"),
            ]
        )
        response = await provider.complete(request)
        assert response.message.content == "hello back"
        assert response.finish_reason is LlmFinishReason.STOP

        headers = captured["headers"]
        assert headers["x-api-key"] == "sk-mock"
        assert headers["anthropic-version"] == "2023-06-01"

        body = captured["body"]
        assert '"system":"You are X."' in body
        # Top-level system field; user message preserved
        assert '"role":"user"' in body and '"content":"hi"' in body

    @pytest.mark.asyncio
    async def test_stream_yields_text_deltas_and_done(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("accept") == "text/event-stream"
            body = "\n".join(
                [
                    "event: message_start",
                    'data: {"type":"message_start"}',
                    "",
                    "event: content_block_delta",
                    'data: {"delta":{"type":"text_delta","text":"hel"}}',
                    "",
                    "event: content_block_delta",
                    'data: {"delta":{"type":"text_delta","text":"lo"}}',
                    "",
                    "event: message_delta",
                    'data: {"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}',
                    "",
                ]
            )
            return httpx.Response(200, text=body)

        provider = AnthropicProvider(
            config=AnthropicProvider.Config(
                name="anthropic-mock",
                base_url="https://mock.local",
                api_key="sk-mock",
                model="claude-test",
                http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
            )
        )
        request = LlmRequest(
            messages=[ChatMessage(role="user", content="hi")], stream=True
        )
        events = [event async for event in provider.stream(request)]
        deltas = [e.delta_text for e in events if e.event == "delta"]
        assert deltas == ["hel", "lo"]
        done_events = [e for e in events if e.event == "done"]
        assert done_events, "stream should emit a done event"
        assert done_events[0].finish_reason is LlmFinishReason.STOP

    @pytest.mark.asyncio
    async def test_healthcheck_with_empty_api_key_short_circuits_unhealthy(self) -> None:
        provider = AnthropicProvider(config=AnthropicProvider.Config(api_key=""))
        status = await provider.healthcheck()
        assert status.healthy is False
