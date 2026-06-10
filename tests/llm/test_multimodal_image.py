"""Image input end-to-end: attachments -> OpenAI image_url content blocks."""

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


def test_normalize_attachment_accepts_url() -> None:
    assert normalize_attachment({"kind": "image", "url": "https://x/cat.png"}) == {
        "kind": "image",
        "url": "https://x/cat.png",
    }
    assert normalize_attachment(
        {"kind": "image", "url": "data:image/png;base64,aGVsbG8="}
    ) == {"kind": "image", "url": "data:image/png;base64,aGVsbG8="}
    # base64 path still works.
    assert normalize_attachment(
        {"kind": "image", "mime_type": "image/png", "data": "aGVsbG8="}
    ) == {"kind": "image", "mime_type": "image/png", "data": "aGVsbG8="}
    # neither url nor data -> rejected.
    assert normalize_attachment({"kind": "image"}) is None
    # non-image scheme rejected.
    assert normalize_attachment({"kind": "image", "url": "ftp://x"}) is None


def test_content_list_emits_url_and_data_images() -> None:
    blocks = build_openai_tool_content_list(
        "describe",
        [
            {"kind": "image", "url": "https://x/cat.png"},
            {"kind": "image", "mime_type": "image/png", "data": "aGVsbG8="},
        ],
    )
    assert blocks[0] == {"type": "text", "text": "describe"}
    urls = [b["image_url"]["url"] for b in blocks if b["type"] == "image_url"]
    assert urls == ["https://x/cat.png", "data:image/png;base64,aGVsbG8="]


def test_payload_emits_image_blocks_for_user_message() -> None:
    request = LlmRequest(
        messages=[
            ChatMessage(
                role="user",
                content="what is this?",
                metadata={"attachments": [{"kind": "image", "url": "https://x/c.png"}]},
            )
        ],
        model="qwen-vl",
    )
    payload = build_openai_completion_payload(
        request, model="qwen-vl", max_tokens_default=1024, extra_body={}, stream=False
    )
    user = payload["messages"][-1]
    assert user["role"] == "user"
    assert isinstance(user["content"], list)
    kinds = [b["type"] for b in user["content"]]
    assert "text" in kinds and "image_url" in kinds
    image = next(b for b in user["content"] if b["type"] == "image_url")
    assert image["image_url"]["url"] == "https://x/c.png"


def test_text_only_user_message_stays_flat_string() -> None:
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    payload = build_openai_completion_payload(
        request, model="m", max_tokens_default=1024, extra_body={}, stream=False
    )
    # No attachments -> plain string content (unchanged behavior).
    assert payload["messages"][-1]["content"] == "hi"
