"""Phase 14 — provider-neutral ``tool_choice`` normalization for OpenAI-compat.

The SDK documents a provider-neutral dict form
``{"type": "tool", "name": "X"}`` so callers don't have to learn each
backend's shape (see ``docs/patterns/forcing-tool-calls.md``). The
OpenAI / OpenRouter / Together / vLLM / Groq endpoints expect a
different envelope:

    {"type": "function", "function": {"name": "X"}}

The OpenAI-compatible adapter normalizes the neutral form to the OpenAI
shape at payload build time. These tests pin the conversion so callers
can rely on it.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import (
    OpenAICompatibleProvider,
    _normalize_tool_choice_for_openai,
)


def _make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            api_key="test",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
        )
    )


def _make_request_with_tool_choice(
    tool_choice: str | dict | None,
) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "chart_vegalite",
                    "description": "Render chart",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice=tool_choice,
    )


# --- Unit-level normalizer --------------------------------------------------


def test_normalize_passes_string_auto_through() -> None:
    assert _normalize_tool_choice_for_openai("auto") == "auto"


def test_normalize_passes_string_required_through() -> None:
    assert _normalize_tool_choice_for_openai("required") == "required"


def test_normalize_passes_string_none_through() -> None:
    assert _normalize_tool_choice_for_openai("none") == "none"


def test_normalize_converts_neutral_dict_to_openai_shape() -> None:
    """``{"type": "tool", "name": "X"}`` (the doc-recommended neutral
    shape) becomes OpenAI's ``{"type": "function", "function": {"name": "X"}}``."""
    result = _normalize_tool_choice_for_openai(
        {"type": "tool", "name": "chart_vegalite"}
    )
    assert result == {
        "type": "function",
        "function": {"name": "chart_vegalite"},
    }


def test_normalize_passes_openai_shape_through_unchanged() -> None:
    """If the caller already provided the OpenAI-native shape, the
    normalizer must NOT double-wrap it."""
    native = {"type": "function", "function": {"name": "chart_vegalite"}}
    assert _normalize_tool_choice_for_openai(native) is native or (
        _normalize_tool_choice_for_openai(native) == native
    )


def test_normalize_passes_unknown_dict_shape_through() -> None:
    """Vendor extensions (custom ``type`` values) are left alone — the
    provider's own error is the source of truth on a malformed payload."""
    unknown = {"type": "vendor_custom", "payload": "x"}
    assert _normalize_tool_choice_for_openai(unknown) == unknown


# --- Integration via the provider payload ----------------------------------


def test_payload_translates_neutral_tool_dict_when_tools_present() -> None:
    """End-to-end: ``LlmRequest.tool_choice`` is the neutral dict, the
    OpenAI payload carries the OpenAI shape."""
    provider = _make_provider()
    request = _make_request_with_tool_choice(
        {"type": "tool", "name": "chart_vegalite"}
    )
    payload = provider._payload(request, stream=False)
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "chart_vegalite"},
    }


def test_payload_keeps_string_required_verbatim() -> None:
    """String forms cross provider boundaries unchanged because both
    OpenAI and Anthropic accept them (or both have a clear equivalent)."""
    provider = _make_provider()
    request = _make_request_with_tool_choice("required")
    payload = provider._payload(request, stream=False)
    assert payload["tool_choice"] == "required"


def test_payload_keeps_openai_native_dict_verbatim() -> None:
    """Callers that already know they're on OpenAI and want to pass the
    native shape get exactly what they passed."""
    provider = _make_provider()
    native = {"type": "function", "function": {"name": "chart_vegalite"}}
    request = _make_request_with_tool_choice(native)
    payload = provider._payload(request, stream=False)
    assert payload["tool_choice"] == native


def test_payload_defaults_to_auto_when_tools_present_and_choice_none() -> None:
    """Legacy behaviour preserved: tools present + no tool_choice =
    ``"auto"``. Normalizer is a no-op on strings."""
    provider = _make_provider()
    request = _make_request_with_tool_choice(None)
    payload = provider._payload(request, stream=False)
    assert payload["tool_choice"] == "auto"


def test_payload_skips_tool_choice_when_no_tools_and_choice_none() -> None:
    """No tools and no choice → no tool_choice key. Avoids accidentally
    flipping backends that interpret the key's presence."""
    provider = _make_provider()
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
    )
    payload = provider._payload(request, stream=False)
    assert "tool_choice" not in payload


def test_payload_carries_choice_through_when_no_tools_but_explicit_choice() -> None:
    """Edge case: ``tool_choice="none"`` with no tools (caller wants
    "definitely no tools, even if framework added some implicitly")."""
    provider = _make_provider()
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        tool_choice="none",
    )
    payload = provider._payload(request, stream=False)
    assert payload["tool_choice"] == "none"
