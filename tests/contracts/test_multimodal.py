"""Typed multimodal attachment + route-capability contracts (domain-neutral)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ChatMessage,
    MultimodalAttachmentRef,
    MultimodalRouteCapabilities,
    attachment_metadata_payload,
    coerce_multimodal_attachments,
    message_with_attachments,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
)

_B64 = "aGVsbG8="  # base64 of b"hello"


# -- MultimodalAttachmentRef ----------------------------------------------------


def test_ref_json_round_trip() -> None:
    ref = MultimodalAttachmentRef(
        kind="image",
        url="https://example/img.png",
        mime_type="image/png",
        filename="img.png",
        size_bytes=1234,
        width=640,
        height=480,
        origin="user_upload",
        trust="untrusted",
        redaction_status="raw",
        summary="a screenshot",
        metadata={"caption": "hi"},
    )
    dumped = ref.model_dump(mode="json")
    assert MultimodalAttachmentRef.model_validate(dumped) == ref


def test_ref_requires_a_locator() -> None:
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="image")  # no attachment_id/uri/url/data


def test_ref_accepts_each_locator_kind() -> None:
    for kwargs in (
        {"attachment_id": "att_1"},
        {"uri": "s3://bucket/key"},
        {"url": "https://x/y.png"},
        {"data": _B64, "mime_type": "image/png"},
    ):
        assert MultimodalAttachmentRef(kind="image", **kwargs)


def test_inline_data_requires_media_metadata() -> None:
    # inline image bytes with no mime_type/format is un-interpretable
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="image", data=_B64)
    # inline audio needs a format
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="audio", data=_B64)
    # ...supplied → valid
    assert MultimodalAttachmentRef(kind="audio", data=_B64, format="wav")
    assert MultimodalAttachmentRef(kind="image", data=_B64, mime_type="image/png")


@pytest.mark.parametrize("field", ["size_bytes", "width", "height", "duration_ms"])
def test_ref_positive_integer_validation(field) -> None:
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="image", url="https://x", **{field: 0})
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="image", url="https://x", **{field: -1})


def test_ref_metadata_must_be_json_safe() -> None:
    with pytest.raises(ValidationError):
        MultimodalAttachmentRef(kind="image", url="https://x", metadata={"o": object()})


# -- helpers --------------------------------------------------------------------


def test_attachment_metadata_payload_projects_minimal_wire() -> None:
    refs = [
        MultimodalAttachmentRef(kind="image", url="https://x/y.png", trust="trusted"),
        MultimodalAttachmentRef(kind="image", data=_B64, mime_type="image/png"),
        MultimodalAttachmentRef(kind="audio", data=_B64, format="wav"),
        # only a non-sendable locator -> projects to just {kind}
        MultimodalAttachmentRef(kind="document", attachment_id="att_9"),
    ]
    payload = attachment_metadata_payload(refs)
    assert payload == [
        {"kind": "image", "url": "https://x/y.png"},
        {"kind": "image", "mime_type": "image/png", "data": _B64},
        {"kind": "audio", "data": _B64, "format": "wav"},
        {"kind": "document"},
    ]


def test_message_with_attachments_appends_and_is_pure() -> None:
    msg = ChatMessage(role=ChatRole.USER, content="look", metadata={"x": 1})
    out = message_with_attachments(
        msg, [MultimodalAttachmentRef(kind="image", url="https://x/y.png")]
    )
    assert out.metadata["attachments"] == [{"kind": "image", "url": "https://x/y.png"}]
    assert out.metadata["x"] == 1  # existing metadata preserved
    assert "attachments" not in msg.metadata  # input untouched
    # a second call appends rather than replaces
    out2 = message_with_attachments(
        out, [MultimodalAttachmentRef(kind="audio", data=_B64, format="wav")]
    )
    assert len(out2.metadata["attachments"]) == 2
    # empty -> unchanged instance
    assert message_with_attachments(msg, []) is msg


def test_coerce_accepts_dicts_and_refs() -> None:
    refs = coerce_multimodal_attachments(
        [
            {"kind": "image", "url": "https://x/y.png"},
            MultimodalAttachmentRef(kind="audio", data=_B64, format="wav"),
        ]
    )
    assert all(isinstance(r, MultimodalAttachmentRef) for r in refs)
    assert coerce_multimodal_attachments(None) == []
    with pytest.raises(ValidationError):
        coerce_multimodal_attachments([{"kind": "image"}])  # no locator


# -- provider interop -----------------------------------------------------------


def test_openai_payload_projects_image_url_block() -> None:
    msg = message_with_attachments(
        ChatMessage(role=ChatRole.USER, content="describe", metadata={}),
        [MultimodalAttachmentRef(kind="image", url="https://x/y.png")],
    )
    payload = build_openai_completion_payload(
        LlmRequest(messages=[msg]),
        model="gpt-4o",
        max_tokens_default=100,
        extra_body={},
        stream=False,
    )
    content = payload["messages"][0]["content"]
    assert {"type": "text", "text": "describe"} in content
    assert {"type": "image_url", "image_url": {"url": "https://x/y.png"}} in content


def test_openai_payload_projects_inline_image_as_data_url() -> None:
    msg = message_with_attachments(
        ChatMessage(role=ChatRole.USER, content="", metadata={}),
        [MultimodalAttachmentRef(kind="image", data=_B64, mime_type="image/png")],
    )
    payload = build_openai_completion_payload(
        LlmRequest(messages=[msg]),
        model="gpt-4o",
        max_tokens_default=100,
        extra_body={},
        stream=False,
    )
    content = payload["messages"][0]["content"]
    assert any(
        b.get("type") == "image_url"
        and b["image_url"]["url"] == f"data:image/png;base64,{_B64}"
        for b in content
    )


# -- MultimodalRouteCapabilities ------------------------------------------------


def test_route_capabilities_round_trip_and_supports_kind() -> None:
    caps = MultimodalRouteCapabilities(
        model_role="image_understanding",
        supports_image_input=True,
        supports_pdf_input=True,
        accepted_mime_types=("image/png", "image/jpeg"),
        max_attachments=8,
        max_attachment_bytes=10_000_000,
        requires_public_urls=False,
        accepts_data_urls=True,
    )
    assert MultimodalRouteCapabilities.model_validate(caps.model_dump()) == caps
    assert caps.supports_kind("image") is True
    assert caps.supports_kind("document") is True
    assert caps.supports_kind("audio") is False
    assert caps.supports_kind("video") is False


@pytest.mark.parametrize("field", ["max_attachments", "max_attachment_bytes"])
def test_route_capabilities_positive_limits(field) -> None:
    with pytest.raises(ValidationError):
        MultimodalRouteCapabilities(**{field: 0})


def test_public_exports_present() -> None:
    from agent_driver import contracts

    for name in (
        "MultimodalAttachmentRef",
        "MultimodalRouteCapabilities",
        "attachment_metadata_payload",
        "coerce_multimodal_attachments",
        "message_with_attachments",
    ):
        assert name in contracts.__all__
        assert hasattr(contracts, name)
