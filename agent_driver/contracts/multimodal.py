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

import re
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
ImageDetail = Literal["auto", "low", "high"]
OcrMode = Literal["auto", "disabled", "prefer", "force"]

_OPENROUTER_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
_QWEN_COMMON_SAFE_IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")
_QWEN3_VISUAL_TOKEN_PIXELS = 32 * 32
_QWEN3_HIGH_RES_MAX_PIXELS = 16_384 * _QWEN3_VISUAL_TOKEN_PIXELS
_QWEN3_DEFAULT_MAX_PIXELS = 2_560 * _QWEN3_VISUAL_TOKEN_PIXELS


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

    # Provider-facing image processing hints. Hosts may leave these unset; known
    # model profiles can fill them for OCR/detail-heavy tasks. They are generic
    # hints, not storage or UI state.
    detail: ImageDetail | None = None
    min_pixels: int | None = None
    max_pixels: int | None = None
    resized_width: int | None = None
    resized_height: int | None = None

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

    @field_validator(
        "size_bytes",
        "width",
        "height",
        "duration_ms",
        "min_pixels",
        "max_pixels",
        "resized_width",
        "resized_height",
    )
    @classmethod
    def _validate_positive(cls, value: int | None, info: Any) -> int | None:
        value = ensure_non_negative_int(value, field_name=info.field_name)
        if value is not None and value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return value

    @model_validator(mode="after")
    def _validate_image_processing_hints(self) -> "MultimodalAttachmentRef":
        if self.min_pixels is not None and self.max_pixels is not None:
            if self.min_pixels > self.max_pixels:
                raise ValueError("min_pixels must be <= max_pixels")
        return self

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


class MultimodalImagePreprocessSettings(ContractModel):
    """Generic image preflight/preprocess hints for a multimodal route.

    The harness does not resize bytes itself. These values tell a host what a
    recognized model route can tolerate and which provider-facing hints are
    sensible for detail/OCR-heavy inputs.
    """

    accepted_mime_types: tuple[str, ...] = ()
    min_width: int | None = None
    min_height: int | None = None
    max_width: int | None = None
    max_height: int | None = None
    max_pixels: int | None = None
    default_max_pixels: int | None = None
    max_encoded_bytes: int | None = None
    max_aspect_ratio: float | None = None
    token_pixels: int | None = None
    resize_multiple: int | None = None
    preferred_detail: ImageDetail = "auto"
    prefer_high_resolution: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "min_width",
        "min_height",
        "max_width",
        "max_height",
        "max_pixels",
        "default_max_pixels",
        "max_encoded_bytes",
        "token_pixels",
        "resize_multiple",
    )
    @classmethod
    def _validate_positive_int(cls, value: int | None, info: Any) -> int | None:
        value = ensure_non_negative_int(value, field_name=info.field_name)
        if value is not None and value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer")
        return value

    @field_validator("max_aspect_ratio")
    @classmethod
    def _validate_positive_float(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("max_aspect_ratio must be positive")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(
            value, field_name="image preprocess metadata"
        )


class MultimodalOcrSettings(ContractModel):
    """Model-neutral OCR policy hints.

    Hosts can expose these as general OCR settings, then merge them with a known
    model profile. The settings remain declarative: no domain-specific evidence
    semantics, storage, or prompt text lives here.
    """

    mode: OcrMode = "auto"
    language_hints: tuple[str, ...] = ()
    preserve_layout: bool = True
    extract_tables: bool = True
    preferred_detail: ImageDetail = "high"
    high_resolution: bool = True
    max_output_chars: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_output_chars")
    @classmethod
    def _validate_max_output_chars(cls, value: int | None) -> int | None:
        value = ensure_non_negative_int(value, field_name="max_output_chars")
        if value is not None and value <= 0:
            raise ValueError("max_output_chars must be a positive integer")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="ocr metadata")


class MultimodalModelProfile(ContractModel):
    """Recognized model-specific multimodal behavior and generic fallbacks."""

    profile_id: str
    provider: str | None = None
    model_pattern: str | None = None
    known_model: bool = False
    capabilities: MultimodalRouteCapabilities = Field(
        default_factory=MultimodalRouteCapabilities
    )
    image_preprocessing: MultimodalImagePreprocessSettings = Field(
        default_factory=MultimodalImagePreprocessSettings
    )
    ocr: MultimodalOcrSettings = Field(default_factory=MultimodalOcrSettings)
    attachment_defaults: dict[str, Any] = Field(default_factory=dict)
    provider_extra_body: dict[str, Any] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @field_validator("attachment_defaults", "provider_extra_body")
    @classmethod
    def _validate_json_dict(cls, value: dict[str, Any], info: Any) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name=info.field_name)


def _canonical_provider(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _canonical_model(value: str | None) -> str:
    return str(value or "").strip().lower()


def _matches(pattern: str, model: str) -> bool:
    return bool(re.fullmatch(pattern, model))


def _qwen3_visual_profile(
    *,
    profile_id: str,
    provider: str,
    model_pattern: str,
    model_role: str,
    supports_video: bool,
    accepted_mime_types: tuple[str, ...],
    max_attachments: int,
    context_window_tokens: int | None,
) -> MultimodalModelProfile:
    metadata: dict[str, Any] = {}
    if context_window_tokens:
        metadata["context_window_tokens"] = context_window_tokens
    return MultimodalModelProfile(
        profile_id=profile_id,
        provider=provider,
        model_pattern=model_pattern,
        known_model=True,
        capabilities=MultimodalRouteCapabilities(
            model_role=model_role,
            supports_image_input=True,
            supports_video_input=supports_video,
            accepted_mime_types=accepted_mime_types,
            max_attachments=max_attachments,
            max_attachment_bytes=10 * 1024 * 1024,
            accepts_data_urls=True,
            metadata=metadata,
        ),
        image_preprocessing=MultimodalImagePreprocessSettings(
            accepted_mime_types=accepted_mime_types,
            min_width=11,
            min_height=11,
            max_width=7680,
            max_height=4320,
            max_pixels=_QWEN3_HIGH_RES_MAX_PIXELS,
            default_max_pixels=_QWEN3_DEFAULT_MAX_PIXELS,
            max_encoded_bytes=10 * 1024 * 1024,
            max_aspect_ratio=200,
            token_pixels=_QWEN3_VISUAL_TOKEN_PIXELS,
            resize_multiple=32,
            preferred_detail="high",
            prefer_high_resolution=True,
            metadata={"source": "qwen_visual_understanding"},
        ),
        ocr=MultimodalOcrSettings(
            mode="prefer",
            preserve_layout=True,
            extract_tables=True,
            preferred_detail="high",
            high_resolution=True,
        ),
        attachment_defaults={
            "detail": "high",
            "max_pixels": _QWEN3_HIGH_RES_MAX_PIXELS,
        },
        provider_extra_body={"vl_high_resolution_images": True},
        notes=(
            "Qwen visual routes benefit from high-resolution OCR settings for small text.",
            "Hosts should still enforce storage, auth, redaction, and retention outside Agent Driver.",
        ),
    )


def multimodal_profile_for_model(
    provider: str | None,
    model: str | None,
    *,
    model_role: str = "vision",
) -> MultimodalModelProfile:
    """Return generic multimodal/OCR hints for a known provider/model pair.

    The table is intentionally small and deterministic. Unknown models return a
    neutral profile with no claimed image support; hosts may still layer runtime
    capability probes on top.
    """

    provider_key = _canonical_provider(provider)
    model_key = _canonical_model(model)
    openrouter_mimes = _OPENROUTER_IMAGE_MIME_TYPES if provider_key == "openrouter" else _QWEN_COMMON_SAFE_IMAGE_MIME_TYPES

    if _matches(r"qwen/qwen3-vl-235b-a22b-(instruct|thinking)", model_key):
        return _qwen3_visual_profile(
            profile_id="qwen3-vl-235b-a22b",
            provider=provider_key or None,
            model_pattern="qwen/qwen3-vl-235b-a22b-(instruct|thinking)",
            model_role=model_role,
            supports_video=False,
            accepted_mime_types=openrouter_mimes,
            max_attachments=250,
            context_window_tokens=262_144,
        )
    if _matches(r"qwen/qwen3\.8-(max|27b|2\.4t-a95b)", model_key):
        return _qwen3_visual_profile(
            profile_id="qwen3.8-visual",
            provider=provider_key or None,
            model_pattern="qwen/qwen3.8-(max|27b|2.4t-a95b)",
            model_role=model_role,
            supports_video=True,
            accepted_mime_types=openrouter_mimes,
            max_attachments=80,
            context_window_tokens=1_000_000,
        )
    if _matches(r"qwen/qwen3\.7-plus", model_key):
        return _qwen3_visual_profile(
            profile_id="qwen3.7-plus-visual",
            provider=provider_key or None,
            model_pattern="qwen/qwen3.7-plus",
            model_role=model_role,
            supports_video=False,
            accepted_mime_types=openrouter_mimes,
            max_attachments=250,
            context_window_tokens=1_000_000,
        )
    if _matches(r"qwen/qwen3(\.7)?-max", model_key):
        return MultimodalModelProfile(
            profile_id="qwen3-text-only",
            provider=provider_key or None,
            model_pattern="qwen/qwen3(.7)?-max",
            known_model=True,
            capabilities=MultimodalRouteCapabilities(model_role=model_role),
            notes=("This Qwen Max route is known as text-only in the provider catalog.",),
        )
    return MultimodalModelProfile(
        profile_id="unknown",
        provider=provider_key or None,
        known_model=False,
        capabilities=MultimodalRouteCapabilities(model_role=model_role),
    )


def attachment_defaults_for_profile(
    profile: MultimodalModelProfile,
    *,
    ocr: bool = False,
) -> dict[str, Any]:
    """Return sendable attachment hints for a profile and task style."""

    hints = dict(profile.attachment_defaults)
    if ocr and profile.ocr.high_resolution:
        hints["detail"] = profile.ocr.preferred_detail
        if profile.image_preprocessing.max_pixels is not None:
            hints["max_pixels"] = profile.image_preprocessing.max_pixels
    return hints


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
    if ref.detail:
        wire["detail"] = ref.detail
    if ref.min_pixels is not None:
        wire["min_pixels"] = ref.min_pixels
    if ref.max_pixels is not None:
        wire["max_pixels"] = ref.max_pixels
    if ref.resized_width is not None:
        wire["resized_width"] = ref.resized_width
    if ref.resized_height is not None:
        wire["resized_height"] = ref.resized_height
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
    "ImageDetail",
    "MultimodalAttachmentRef",
    "MultimodalImagePreprocessSettings",
    "MultimodalModelProfile",
    "MultimodalOcrSettings",
    "MultimodalRouteCapabilities",
    "OcrMode",
    "RedactionStatus",
    "attachment_metadata_payload",
    "attachment_defaults_for_profile",
    "coerce_multimodal_attachments",
    "message_with_attachments",
    "multimodal_profile_for_model",
]
