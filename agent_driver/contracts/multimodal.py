"""Typed multimodal attachment + route-capability contracts (domain-neutral).

Agent Driver owns only the **typed envelope** for a media attachment and its **projection
into the provider-facing wire form** — the ``ChatMessage.metadata["attachments"]``
convention the OpenAI-compatible payload builder and tool-result unpacker already consume.
Everything about the *bytes* — storage, auth, redaction, malware scanning, retention, and
UI — is **host-owned**: this module never fetches, decodes, validates, or persists media.

The fields here are deliberately generic. ``trust`` / ``redaction_status`` are coarse,
product-neutral labels (not a specific application's evidence taxonomy); ``origin`` is a
free-form host-defined string. An attachment is a *context input*, not an instruction.

Model routing: image/vision understanding and the main reasoning model are frequently
different routes. This module does **not** add a parallel router — it describes a route's
capabilities via :class:`MultimodalRouteCapabilities` keyed by a generic ``model_role``
(e.g. ``default`` / ``vision`` / ``image_understanding`` / ``ocr`` /
``audio_transcription``) that composes with the existing model-role / provider routing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)

AttachmentKind = Literal["image", "audio", "video", "document", "other"]
# Coarse, product-neutral provenance labels (NOT a domain evidence taxonomy).
AttachmentTrust = Literal["trusted", "untrusted", "unknown"]
RedactionStatus = Literal["raw", "redacted", "unknown"]


class MultimodalAttachmentRef(ContractModel):
    """A typed reference to one media attachment.

    Carries at least one **locator** (``attachment_id`` / ``uri`` / ``url`` / inline
    base64 ``data``) plus optional media + provenance metadata. Only ``url`` and inline
    ``data`` are directly sendable to a provider; ``attachment_id`` / ``uri`` are
    host-storage handles the host resolves to bytes before a turn.
    """

    kind: AttachmentKind = "image"

    # Locators — at least one is required.
    attachment_id: str | None = None
    uri: str | None = None
    url: str | None = None
    data: str | None = None  # inline base64 (no data:-URL prefix)

    # Media metadata (all optional; host-supplied).
    mime_type: str | None = None
    format: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None

    # Provenance / trust — generic, host-defined.
    origin: str | None = None
    trust: AttachmentTrust = "unknown"
    redaction_status: RedactionStatus = "unknown"

    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="attachment metadata")

    @field_validator("size_bytes", "width", "height", "duration_ms")
    @classmethod
    def _validate_positive(cls, value: int | None, info: Any) -> int | None:
        value = ensure_non_negative_int(value, field_name=info.field_name)
        if value is not None and value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return value

    @model_validator(mode="after")
    def _validate_locator_and_inline(self) -> "MultimodalAttachmentRef":
        if not any((self.attachment_id, self.uri, self.url, self.data)):
            raise ValueError(
                "attachment requires at least one locator: "
                "attachment_id, uri, url, or data"
            )
        # Inline bytes are un-interpretable without a mime type / format tag, and the
        # provider projection needs one to build a native content block.
        if self.data:
            if self.kind == "audio" and not self.format:
                raise ValueError("inline audio data requires a format")
            if self.kind != "audio" and not (self.mime_type or self.format):
                raise ValueError("inline data requires mime_type or format")
        return self


class MultimodalRouteCapabilities(ContractModel):
    """What one model route/role can accept as multimodal input (and emit).

    Describes a route keyed by a generic ``model_role``; a host maps that role through the
    existing model-role / provider routing so the main reasoning model and a
    vision/image-understanding model can be **different** routes. Purely declarative — the
    harness does not enforce these; a host may use them for pre-flight validation.
    """

    model_role: str = "vision"
    supports_image_input: bool = False
    supports_audio_input: bool = False
    supports_video_input: bool = False
    supports_pdf_input: bool = False
    supports_output_audio: bool = False
    accepted_mime_types: tuple[str, ...] = ()
    accepted_formats: tuple[str, ...] = ()
    max_attachments: int | None = None
    max_attachment_bytes: int | None = None
    requires_public_urls: bool = False
    accepts_data_urls: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="route capability metadata")

    @field_validator("max_attachments", "max_attachment_bytes")
    @classmethod
    def _validate_positive(cls, value: int | None, info: Any) -> int | None:
        value = ensure_non_negative_int(value, field_name=info.field_name)
        if value is not None and value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return value

    def supports_kind(self, kind: str) -> bool:
        """True when this route accepts the given attachment ``kind`` as input."""
        return {
            "image": self.supports_image_input,
            "audio": self.supports_audio_input,
            "video": self.supports_video_input,
            "document": self.supports_pdf_input,
        }.get(kind, False)


def _ref_to_wire(ref: MultimodalAttachmentRef) -> dict[str, Any]:
    """Project one typed ref to the minimal provider-facing attachment dict.

    Emits exactly the keys the existing wire convention consumes — ``kind`` plus the
    ``url`` / ``mime_type`` + ``data`` / ``format`` locators — so it interoperates with
    ``build_openai_tool_content_list`` / ``normalize_attachment`` unchanged. A ref with
    only a non-sendable locator (``attachment_id`` / ``uri``) projects to just ``{kind}``,
    which the provider path drops gracefully (the host must resolve bytes first).
    """
    wire: dict[str, Any] = {"kind": ref.kind}
    if ref.url:
        wire["url"] = ref.url
    if ref.mime_type:
        wire["mime_type"] = ref.mime_type
    if ref.data:
        wire["data"] = ref.data
    if ref.format:
        wire["format"] = ref.format
    return wire


def attachment_metadata_payload(
    attachments: "list[MultimodalAttachmentRef | dict[str, Any]] | None",
) -> list[dict[str, Any]]:
    """Convert typed refs (or dicts) into provider-facing ``metadata['attachments']`` dicts.

    Accepts already-typed :class:`MultimodalAttachmentRef` objects or JSON-like dicts
    (validated through the contract first). The result is the wire form the OpenAI-compat
    payload builder projects into native ``content`` blocks.
    """
    payload: list[dict[str, Any]] = []
    for item in coerce_multimodal_attachments(attachments):
        payload.append(_ref_to_wire(item))
    return payload


def message_with_attachments(
    message: ChatMessage,
    attachments: "list[MultimodalAttachmentRef | dict[str, Any]] | None",
) -> ChatMessage:
    """Return a copy of ``message`` with ``attachments`` appended under
    ``metadata['attachments']`` (the existing wire convention).

    Preserves any attachments already present and never mutates the input. When
    ``attachments`` is empty the message is returned unchanged.
    """
    projected = attachment_metadata_payload(attachments)
    if not projected:
        return message
    existing = message.metadata.get("attachments")
    merged = (list(existing) if isinstance(existing, list) else []) + projected
    return message.model_copy(
        update={"metadata": {**message.metadata, "attachments": merged}}
    )


def coerce_multimodal_attachments(
    value: Any,
) -> list[MultimodalAttachmentRef]:
    """Validate a JSON-like attachment value into a list of typed refs.

    Accepts ``None`` (→ empty), a single ref/dict, or a list of them; each dict is
    validated through :class:`MultimodalAttachmentRef` (raising on a malformed entry).
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    refs: list[MultimodalAttachmentRef] = []
    for item in items:
        if isinstance(item, MultimodalAttachmentRef):
            refs.append(item)
        else:
            refs.append(MultimodalAttachmentRef.model_validate(item))
    return refs


__all__ = [
    "AttachmentKind",
    "AttachmentTrust",
    "MultimodalAttachmentRef",
    "MultimodalRouteCapabilities",
    "RedactionStatus",
    "attachment_metadata_payload",
    "coerce_multimodal_attachments",
    "message_with_attachments",
]
