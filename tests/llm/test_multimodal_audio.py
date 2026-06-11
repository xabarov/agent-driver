"""Audio input end-to-end: attachments -> OpenAI input_audio content blocks."""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
)
from agent_driver.llm.tool_result_unpacker import (
    build_openai_tool_content_list,
    normalize_attachment,
)
from agent_driver.server.openai.schema import ChatMessageIn

# base64 of b"hello" — a valid-base64 stand-in for audio bytes.
_B64 = "aGVsbG8="


def test_normalize_attachment_accepts_audio() -> None:
    assert normalize_attachment(
        {"kind": "audio", "data": _B64, "format": "wav"}
    ) == {"kind": "audio", "data": _B64, "format": "wav"}
    # format is lower-cased / canonicalized.
    assert normalize_attachment({"kind": "audio", "data": _B64, "format": "MP3"}) == {
        "kind": "audio",
        "data": _B64,
        "format": "mp3",
    }


def test_normalize_audio_rejects_bad_inputs() -> None:
    # unknown format dropped (backend would 400 on it).
    assert normalize_attachment({"kind": "audio", "data": _B64, "format": "ogg"}) is None
    # missing format dropped.
    assert normalize_attachment({"kind": "audio", "data": _B64}) is None
    # empty / missing data dropped.
    assert normalize_attachment({"kind": "audio", "data": "", "format": "wav"}) is None
    assert normalize_attachment({"kind": "audio", "format": "wav"}) is None
    # corrupt base64 dropped.
    assert (
        normalize_attachment({"kind": "audio", "data": "!!!", "format": "wav"}) is None
    )


def test_content_list_emits_input_audio_block() -> None:
    blocks = build_openai_tool_content_list(
        "transcribe",
        [{"kind": "audio", "data": _B64, "format": "wav"}],
    )
    assert blocks[0] == {"type": "text", "text": "transcribe"}
    audio = next(b for b in blocks if b["type"] == "input_audio")
    assert audio["input_audio"] == {"data": _B64, "format": "wav"}


def test_content_list_mixes_image_and_audio() -> None:
    blocks = build_openai_tool_content_list(
        "describe both",
        [
            {"kind": "image", "url": "https://x/cat.png"},
            {"kind": "audio", "data": _B64, "format": "mp3"},
        ],
    )
    kinds = [b["type"] for b in blocks]
    assert kinds == ["text", "image_url", "input_audio"]


def test_payload_emits_input_audio_for_user_message() -> None:
    request = LlmRequest(
        messages=[
            ChatMessage(
                role="user",
                content="what is said?",
                metadata={
                    "attachments": [{"kind": "audio", "data": _B64, "format": "wav"}]
                },
            )
        ],
        model="gpt-audio",
    )
    payload = build_openai_completion_payload(
        request, model="gpt-audio", max_tokens_default=1024, extra_body={}, stream=False
    )
    user = payload["messages"][-1]
    assert isinstance(user["content"], list)
    audio = next(b for b in user["content"] if b["type"] == "input_audio")
    assert audio["input_audio"]["format"] == "wav"


# --- inbound schema parsing -------------------------------------------------


def test_schema_audio_attachments_parses_input_audio_parts() -> None:
    msg = ChatMessageIn(
        role="user",
        content=[
            {"type": "text", "text": "transcribe"},
            {"type": "input_audio", "input_audio": {"data": _B64, "format": "wav"}},
        ],
    )
    assert msg.audio_attachments() == [{"kind": "audio", "data": _B64, "format": "wav"}]
    # text flattening still skips the audio part.
    assert msg.text_content() == "transcribe"


def test_schema_media_attachments_merges_image_and_audio() -> None:
    msg = ChatMessageIn(
        role="user",
        content=[
            {"type": "image_url", "image_url": {"url": "https://x/c.png"}},
            {"type": "input_audio", "input_audio": {"data": _B64, "format": "mp3"}},
        ],
    )
    media = msg.media_attachments()
    assert [m["kind"] for m in media] == ["image", "audio"]


def test_schema_audio_attachments_skips_malformed_parts() -> None:
    msg = ChatMessageIn(
        role="user",
        content=[
            {"type": "input_audio", "input_audio": "not a dict"},
            {"type": "input_audio", "input_audio": {"data": "", "format": "wav"}},
            {"type": "input_audio"},
        ],
    )
    assert msg.audio_attachments() == []
