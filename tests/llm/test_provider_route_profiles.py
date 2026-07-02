"""Deterministic provider route-profile and preflight tests."""

from __future__ import annotations

import json

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.provider_capabilities import (
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.provider_route_profiles import (
    preview_provider_preflight,
    resolve_openai_compatible_route_profile,
)
from agent_driver.llm.provider_descriptors import ProviderSpec, resolve_provider
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def _profile(provider_name: str, base_url: str, model: str):
    capability = resolve_openai_compatible_capabilities(
        provider_name=provider_name,
        base_url=base_url,
        model=model,
    )
    return resolve_openai_compatible_route_profile(
        provider_name=provider_name,
        base_url=base_url,
        model=model,
        capability_profile=capability,
    )


def _strict_json_request() -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="json")],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        },
    )


def test_route_profile_resolution_openrouter_openai_vllm_unknown() -> None:
    openrouter = _profile(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "openai/gpt-5.5",
    )
    openai = _profile("openai", "https://api.openai.com/v1", "gpt-4.1")
    vllm = _profile("vllm", "http://localhost:8000/v1", "qwen3-32b")
    unknown = _profile("gateway", "https://llm.example.test/v1", "model-x")

    assert openrouter.provider_id == "openrouter"
    assert openrouter.base_url_family == "openrouter"
    assert openrouter.supports_forced_tool_choice is False
    assert openrouter.supports_reasoning_details is True
    assert openrouter.emits_provider_request_id is True

    assert openai.provider_id == "openai"
    assert openai.base_url_family == "openai"
    assert openai.supports_forced_tool_choice is True
    assert openai.supports_strict_json_schema is True
    assert openai.emits_provider_request_id is True

    assert vllm.base_url_family == "local"
    assert vllm.thinking_extra_body_mode == "chat_template_kwargs.enable_thinking"
    assert vllm.supports_strict_json_schema is False

    assert unknown.base_url_family == "unknown"
    assert unknown.supports_forced_tool_choice is False
    assert unknown.supports_strict_json_schema is False


def test_forced_tool_choice_supported_vs_downgraded() -> None:
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="use tool")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice={"type": "tool", "name": "lookup"},
    )
    openai_profile = _profile("openai", "https://api.openai.com/v1", "gpt-4.1")
    openrouter_profile = _profile(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "openai/gpt-5.5",
    )
    capability_openai = resolve_openai_compatible_capabilities(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
    )
    capability_openrouter = resolve_openai_compatible_capabilities(
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-5.5",
    )

    supported = preview_provider_preflight(
        provider_name="openai",
        provider_kind="openai_compatible",
        model="gpt-4.1",
        route_profile=openai_profile,
        capability_profile=capability_openai,
        request=request,
    )
    downgraded = preview_provider_preflight(
        provider_name="openrouter",
        provider_kind="openai_compatible",
        model="openai/gpt-5.5",
        route_profile=openrouter_profile,
        capability_profile=capability_openrouter,
        request=request,
    )

    assert supported.status == "ok"
    assert supported.request_shape["tool_choice_policy"] == (
        "forced_tool_choice_supported"
    )
    assert downgraded.status == "degraded"
    assert downgraded.request_shape["tool_choice_policy"] == (
        "forced_tool_choice_downgraded_to_auto"
    )
    assert "forced_tool_choice" in downgraded.downgrades


def test_strict_json_supported_vs_downgraded() -> None:
    request = _strict_json_request()
    openai_profile = _profile("openai", "https://api.openai.com/v1", "gpt-4.1")
    unknown_profile = _profile("gateway", "https://llm.example.test/v1", "model-x")
    capability_openai = resolve_openai_compatible_capabilities(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
    )
    capability_unknown = resolve_openai_compatible_capabilities(
        provider_name="gateway",
        base_url="https://llm.example.test/v1",
        model="model-x",
    )

    supported = preview_provider_preflight(
        provider_name="openai",
        provider_kind="openai_compatible",
        model="gpt-4.1",
        route_profile=openai_profile,
        capability_profile=capability_openai,
        request=request,
    )
    downgraded = preview_provider_preflight(
        provider_name="gateway",
        provider_kind="openai_compatible",
        model="model-x",
        route_profile=unknown_profile,
        capability_profile=capability_unknown,
        request=request,
    )

    assert supported.request_shape["response_format_policy"] == (
        "strict_json_schema_supported"
    )
    assert downgraded.request_shape["response_format_policy"] == (
        "strict_json_schema_downgraded_to_json_object_or_validation"
    )
    assert "strict_json_schema" in downgraded.downgrades


def test_reasoning_and_request_id_policy_visible_on_provider_status() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="token",
            model="qwen/qwen3-235b-a22b-2507",
        )
    )

    route_profile = provider.status.metadata["route_profile"]
    preflight = provider.status.metadata["provider_preflight"]

    assert route_profile["supports_reasoning"] is True
    assert route_profile["supports_reasoning_details"] is True
    assert route_profile["thinking_extra_body_mode"] == "openrouter_reasoning"
    assert route_profile["emits_provider_request_id"] is True
    assert preflight["request_shape"]["reasoning_policy"] == (
        "reasoning_supported:openrouter_reasoning"
    )
    assert preflight["request_shape"]["emits_provider_request_id"] is True


def test_descriptor_resolved_openai_compatible_provider_exposes_route_profile() -> None:
    provider = resolve_provider(
        ProviderSpec(
            provider_id="openrouter",
            api_key="token",
            model="openai/gpt-5.5",
        ),
        env={},
    )

    route_profile = provider.status.metadata["route_profile"]
    preflight = provider.status.metadata["provider_preflight"]

    assert route_profile["provider_id"] == "openrouter"
    assert route_profile["base_url_family"] == "openrouter"
    assert preflight["route_profile_id"] == route_profile["profile_id"]


def test_preflight_summary_is_redaction_safe() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1?api_key=do-not-leak",
            api_key="sk-secret",
            model="openai/gpt-5.5",
        )
    )

    payload = provider.status.metadata["provider_preflight"]
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["redaction"]["safe_by_default"] is True
    assert payload["redaction"]["contains_api_key"] is False
    assert payload["redaction"]["contains_raw_base_url"] is False
    assert "sk-secret" not in encoded
    assert "do-not-leak" not in encoded
    assert "openrouter.ai" not in encoded
