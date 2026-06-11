"""Phase 13 H29.2 — tests for tool result attachment unpacking.

Pins:
  * ``normalize_attachment`` accepts well-formed image attachments and
    rejects malformed inputs (missing mime, bad base64, unknown kind,
    oversized payload, non-dict).
  * ``extract_attachments_from_structured_output`` splits the
    ``attachments`` list off and returns a shallow-copy of the
    remaining structured payload — original input untouched on the
    "all entries malformed" path.
  * ``build_openai_tool_content_list`` produces the OpenAI ``content``
    list shape with text + ``image_url`` blocks; empty inputs return
    None so callers keep flat-string codepath; no-text-but-attachments
    inserts an empty text block (required by some backends).
  * ``OpenAICompatibleProvider._payload`` actually emits the content
    list shape on tool-role messages with attachments, and keeps the
    flat-string codepath for everything else.
"""

from __future__ import annotations

import base64

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.llm.tool_result_unpacker import (
    build_openai_tool_content_list,
    extract_attachments_from_structured_output,
    normalize_attachment,
)

# A 1x1 transparent PNG, base64-encoded.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAU"
    "AAarVyFEAAAAASUVORK5CYII="
)


def _image_attachment(*, mime: str = "image/png", data: str = _TINY_PNG_B64) -> dict:
    return {"kind": "image", "mime_type": mime, "data": data}


# --- normalize_attachment ---------------------------------------------------


def test_normalize_valid_image_passes():
    out = normalize_attachment(_image_attachment())
    assert out == {
        "kind": "image",
        "mime_type": "image/png",
        "data": _TINY_PNG_B64,
    }


def test_normalize_missing_mime_dropped():
    assert normalize_attachment({"kind": "image", "data": _TINY_PNG_B64}) is None


def test_normalize_invalid_mime_dropped():
    assert (
        normalize_attachment(
            {"kind": "image", "mime_type": "png", "data": _TINY_PNG_B64}
        )
        is None
    )


def test_normalize_empty_data_dropped():
    assert (
        normalize_attachment({"kind": "image", "mime_type": "image/png", "data": ""})
        is None
    )


def test_normalize_unknown_kind_dropped():
    """Future ``kind`` values (audio, video) are added on a separate slice;
    today only ``image`` is recognized."""
    assert (
        normalize_attachment(
            {"kind": "audio", "mime_type": "audio/wav", "data": _TINY_PNG_B64}
        )
        is None
    )


def test_normalize_corrupt_base64_dropped():
    assert (
        normalize_attachment(
            {"kind": "image", "mime_type": "image/png", "data": "!!!not base64!!!"}
        )
        is None
    )


def test_normalize_default_kind_is_image():
    """Legacy entries that omit ``kind`` get image-by-default so callers
    that only ever attach images don't need to set it."""
    out = normalize_attachment({"mime_type": "image/png", "data": _TINY_PNG_B64})
    assert out is not None
    assert out["kind"] == "image"


def test_normalize_non_dict_dropped():
    assert normalize_attachment("just a string") is None
    assert normalize_attachment(None) is None
    assert normalize_attachment(42) is None


def test_normalize_oversized_dropped():
    # Build a base64 payload longer than 20MB — drop without crashing.
    huge = "A" * (20 * 1024 * 1024 + 1024)
    assert (
        normalize_attachment({"kind": "image", "mime_type": "image/png", "data": huge})
        is None
    )


# --- extract_attachments_from_structured_output -----------------------------


def test_extract_returns_unchanged_when_no_attachments():
    structured = {"summary": "hello", "items": [1, 2, 3]}
    out, attachments = extract_attachments_from_structured_output(structured)
    assert out is structured
    assert attachments == []


def test_extract_splits_attachments_off():
    structured = {
        "summary": "screenshot saved",
        "attachments": [_image_attachment()],
    }
    out, attachments = extract_attachments_from_structured_output(structured)
    assert "attachments" not in out
    assert out["summary"] == "screenshot saved"
    # Original input not mutated — caller can still log the raw payload.
    assert "attachments" in structured
    assert len(attachments) == 1
    assert attachments[0]["mime_type"] == "image/png"


def test_extract_drops_malformed_entries_keeps_siblings():
    structured = {
        "summary": "two screenshots",
        "attachments": [
            _image_attachment(),
            {"mime_type": "broken"},  # malformed
            _image_attachment(mime="image/jpeg"),
        ],
    }
    out, attachments = extract_attachments_from_structured_output(structured)
    assert "attachments" not in out
    assert len(attachments) == 2
    assert attachments[0]["mime_type"] == "image/png"
    assert attachments[1]["mime_type"] == "image/jpeg"


def test_extract_all_malformed_keeps_structured_untouched():
    """When every attachment is bad, do NOT silently swallow the diagnostic
    noise — leave the input untouched so callers see the bug."""
    structured = {
        "summary": "broken",
        "attachments": [{"mime_type": "broken"}, "not a dict"],
    }
    out, attachments = extract_attachments_from_structured_output(structured)
    assert out is structured
    assert attachments == []


def test_extract_non_dict_input_unchanged():
    out, attachments = extract_attachments_from_structured_output("a string")
    assert out == "a string"
    assert attachments == []


# --- build_openai_tool_content_list -----------------------------------------


def test_build_returns_none_for_empty_attachments():
    assert build_openai_tool_content_list("hello", []) is None


def test_build_assembles_text_then_image_blocks():
    out = build_openai_tool_content_list(
        '{"summary": "screenshot"}',
        [_image_attachment()],
    )
    assert out is not None
    assert out[0] == {"type": "text", "text": '{"summary": "screenshot"}'}
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_with_multiple_images():
    out = build_openai_tool_content_list(
        "two shots",
        [
            _image_attachment(),
            _image_attachment(mime="image/jpeg"),
        ],
    )
    assert out is not None
    # 1 text + 2 image_url
    assert len(out) == 3
    assert out[1]["image_url"]["url"].startswith("data:image/png;")
    assert out[2]["image_url"]["url"].startswith("data:image/jpeg;")


def test_build_no_text_inserts_empty_text_block():
    """OpenAI-compat backends 400 on a content list of only-non-text blocks;
    a leading empty text block satisfies the format."""
    out = build_openai_tool_content_list("", [_image_attachment()])
    assert out is not None
    assert out[0] == {"type": "text", "text": ""}
    assert out[1]["type"] == "image_url"


# --- OpenAICompatibleProvider._payload --------------------------------------


def _make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            api_key="test",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
        )
    )


def test_payload_tool_message_with_attachments_emits_content_list():
    provider = _make_provider()
    request = LlmRequest(
        messages=[
            ChatMessage(role=ChatRole.USER, content="screenshot this"),
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "screenshot", "arguments": "{}"},
                        },
                    ]
                },
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_1",
                content='{"summary": "saved"}',
                metadata={"attachments": [_image_attachment()]},
            ),
        ]
    )
    payload = provider._payload(request, stream=False)
    tool_msg = payload["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert isinstance(tool_msg["content"], list)
    assert tool_msg["content"][0] == {"type": "text", "text": '{"summary": "saved"}'}
    assert tool_msg["content"][1]["type"] == "image_url"
    assert tool_msg["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_payload_tool_message_without_attachments_stays_flat_string():
    """Backwards compat: tool messages without attachments keep the existing
    flat-string content shape so no behaviour change for current callers."""
    provider = _make_provider()
    request = LlmRequest(
        messages=[
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_1",
                content='{"summary": "ok"}',
            ),
        ]
    )
    payload = provider._payload(request, stream=False)
    assert payload["messages"][0]["content"] == '{"summary": "ok"}'


def test_payload_user_role_emits_image_blocks_from_attachments():
    """A user-role message carrying image attachments emits the OpenAI
    content-list (text + image_url) — multimodal image input reaches a vision
    model, not only tool-role screenshots."""
    provider = _make_provider()
    request = LlmRequest(
        messages=[
            ChatMessage(
                role=ChatRole.USER,
                content="hi",
                metadata={"attachments": [_image_attachment()]},
            ),
        ]
    )
    payload = provider._payload(request, stream=False)
    content = payload["messages"][0]["content"]
    assert isinstance(content, list)
    assert any(block["type"] == "image_url" for block in content)


def test_payload_empty_attachments_list_stays_flat_string():
    provider = _make_provider()
    request = LlmRequest(
        messages=[
            ChatMessage(
                role=ChatRole.TOOL,
                tool_call_id="call_1",
                content='{"summary": "ok"}',
                metadata={"attachments": []},
            ),
        ]
    )
    payload = provider._payload(request, stream=False)
    assert payload["messages"][0]["content"] == '{"summary": "ok"}'


# --- end-to-end base64 sanity ---------------------------------------------


def test_data_url_decodes_back_to_original_bytes():
    """Round-trip: data URL → strip header → base64 decode → original PNG bytes."""
    out = build_openai_tool_content_list("", [_image_attachment()])
    assert out is not None
    url = out[1]["image_url"]["url"]
    prefix, b64 = url.split(",", 1)
    assert prefix == "data:image/png;base64"
    raw = base64.b64decode(b64)
    # 1x1 PNG starts with the PNG magic header.
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
