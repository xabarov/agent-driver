"""Provider capability profile tests."""

from __future__ import annotations

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.base import HttpClientConfig
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.provider_capabilities import (
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def test_openrouter_gpt_reasoning_profile() -> None:
    profile = resolve_openai_compatible_capabilities(
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-5.5",
    )

    assert profile.provider_id == "openrouter"
    assert profile.base_url_family == "openrouter"
    assert profile.supports_tool_calls is True
    assert profile.supports_reasoning is True
    assert profile.supports_reasoning_details is True


def test_openrouter_deepseek_v4_reasoning_profile() -> None:
    profile = resolve_openai_compatible_capabilities(
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-flash",
    )

    assert profile.provider_id == "openrouter"
    assert profile.supports_reasoning is True
    assert profile.supports_reasoning_details is True


def test_unknown_openai_compatible_uses_safe_defaults() -> None:
    profile = resolve_openai_compatible_capabilities(
        provider_name="custom",
        base_url="https://llm.example.test/v1",
        model="some-model",
    )

    assert profile.provider_id == "custom"
    assert profile.base_url_family == "unknown"
    assert profile.supports_streaming is True
    assert profile.supports_tool_calls is True
    assert profile.supports_reasoning is False
    assert (
        "capabilities_are_safe_defaults_for_unknown_openai_compatible" in profile.notes
    )


def test_openai_compatible_provider_status_exposes_profile() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="token",
            model="qwen/qwen3-235b-a22b-2507",
        )
    )

    metadata = provider.status.metadata
    profile = metadata.get("capability_profile")
    assert isinstance(profile, dict)
    assert profile["provider_id"] == "openrouter"
    assert profile["supports_reasoning"] is True


def test_openai_compatible_payload_echoes_assistant_reasoning_details() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="token",
            model="openai/gpt-5.5",
        )
    )
    request = LlmRequest(
        messages=[
            ChatMessage(role=ChatRole.USER, content="research"),
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web_fetch",
                                "arguments": '{"url":"https://example.com"}',
                            },
                        }
                    ],
                    "reasoning_details": [
                        {
                            "type": "reasoning.encrypted",
                            "data": "opaque",
                            "id": "r1",
                            "format": "openai-responses-v1",
                            "index": 0,
                        }
                    ],
                },
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_1",
                content='{"summary":"ok"}',
            ),
        ]
    )

    payload = provider._payload(request, stream=False)

    assert payload["messages"][1]["reasoning_details"] == [
        {
            "type": "reasoning.encrypted",
            "data": "opaque",
            "id": "r1",
            "format": "openai-responses-v1",
            "index": 0,
        }
    ]


def test_openai_compatible_payload_merges_request_provider_extra_body() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="token",
            model="deepseek/deepseek-v4-flash",
        )
    )
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="final")],
        metadata={
            "provider_extra_body": {"reasoning": {"enabled": False, "exclude": True}}
        },
    )

    payload = provider._payload(request, stream=False)

    assert payload["reasoning"] == {"enabled": False, "exclude": True}


@pytest.mark.asyncio
async def test_openai_completion_metadata_exposes_profile_and_reasoning_presence() -> (
    None
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-5.5",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                            "reasoning_details": [
                                {"type": "summary", "text": "hidden"}
                            ],
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="token",
            model="openai/gpt-5.5",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handler)),
        )
    )

    response = await provider.complete(
        LlmRequest(messages=[ChatMessage(role=ChatRole.USER, content="hello")])
    )

    assert response.metadata["provider_profile"]["provider_id"] == "openrouter"
    assert response.metadata["provider_reasoning_details_present"] is True
    assert response.metadata["provider_reasoning_details_count"] == 1
    assert response.metadata["provider_reasoning_details"] == [
        {"type": "summary", "text": "hidden"}
    ]
