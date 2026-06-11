"""Phase 13 H26 — tests for ``LlmRequest.response_format`` structured output.

Pins:
  * Default (response_format=None): payload does NOT include the key,
    backwards compat with pre-H26 callers.
  * ``{"type": "json_object"}`` passes through verbatim.
  * ``{"type": "json_schema", "json_schema": {...}}`` passes through verbatim
    and survives the structural validator.
  * Validator: rejects non-dict, rejects ``json_schema`` without
    ``json_schema`` envelope or without required ``name`` / ``schema``.
  * Vendor ``extra_body`` still wins on key collision (Phase 8 contract).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def _make_provider(*, extra_body: dict | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            api_key="test",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
            extra_body=extra_body,
        )
    )


def _make_request(
    *,
    response_format: dict | None = None,
) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hello")],
        response_format=response_format,
    )


# --- Contract / validator ---------------------------------------------------


def test_default_field_value_is_none():
    request = LlmRequest(messages=[ChatMessage(role=ChatRole.USER, content="x")])
    assert request.response_format is None


def test_openai_payload_uses_bounded_default_max_tokens_and_omits_null_temperature():
    payload = _make_provider()._payload(_make_request(), stream=True)
    assert payload["max_tokens"] == 4096
    assert "temperature" not in payload


def test_accepts_json_object_shape():
    request = _make_request(response_format={"type": "json_object"})
    assert request.response_format == {"type": "json_object"}


def test_accepts_json_schema_shape():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "findings_envelope",
            "schema": {
                "type": "object",
                "properties": {"findings": {"type": "array"}},
                "required": ["findings"],
            },
            "strict": True,
        },
    }
    request = _make_request(response_format=rf)
    assert request.response_format == rf


def test_unknown_type_passes_through():
    """Vendor extensions (custom ``type`` values) are NOT gate-kept — the
    underlying provider returns its own error if unknown. Forward compat."""
    request = _make_request(
        response_format={"type": "vendor_special", "payload": "anything"}
    )
    assert request.response_format == {
        "type": "vendor_special",
        "payload": "anything",
    }


def test_rejects_non_dict():
    with pytest.raises(ValidationError) as exc:
        _make_request(response_format="json_object")  # type: ignore[arg-type]
    assert "response_format" in str(exc.value).lower()


def test_rejects_json_schema_missing_envelope():
    with pytest.raises(ValidationError) as exc:
        _make_request(response_format={"type": "json_schema"})
    assert "json_schema" in str(exc.value)


def test_rejects_json_schema_missing_name():
    with pytest.raises(ValidationError) as exc:
        _make_request(
            response_format={
                "type": "json_schema",
                "json_schema": {"schema": {"type": "object"}},
            }
        )
    assert "name" in str(exc.value)


def test_rejects_json_schema_non_dict_schema():
    with pytest.raises(ValidationError) as exc:
        _make_request(
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": "not-a-dict"},
            }
        )
    assert "schema" in str(exc.value)


# --- Provider payload -------------------------------------------------------


def test_default_omits_response_format_key():
    provider = _make_provider()
    payload = provider._payload(_make_request(), stream=False)
    assert "response_format" not in payload


def test_json_object_emits_in_payload():
    provider = _make_provider()
    payload = provider._payload(
        _make_request(response_format={"type": "json_object"}),
        stream=False,
    )
    assert payload["response_format"] == {"type": "json_object"}


def test_json_schema_emits_in_payload():
    provider = _make_provider()
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "findings_envelope",
            "schema": {"type": "object", "properties": {}},
            "strict": True,
        },
    }
    payload = provider._payload(_make_request(response_format=rf), stream=False)
    assert payload["response_format"] == rf


def test_extra_body_overrides_response_format():
    """Phase 8 contract — vendor ``extra_body`` is shallow-merged LAST,
    so vendor keys win on collision. Lets a vLLM provider config rewrite
    H26 ``response_format`` into ``guided_json`` if needed."""
    provider = _make_provider(
        extra_body={"response_format": {"type": "vendor_override"}}
    )
    payload = provider._payload(
        _make_request(response_format={"type": "json_object"}),
        stream=False,
    )
    assert payload["response_format"] == {"type": "vendor_override"}
