"""Phase 13 H29.2 — tool result attachment unpacking.

Tools that produce binary content (screenshots, OCR images, file
artifacts) used to lose that content when their result was serialized
into the next LLM turn: ``json.dumps(tool_payload)`` either crashed on
``bytes`` or string-coerced them into unreadable noise. The model
never saw the image and could not reason over it.

This module formalizes a lightweight convention:

  * A tool's ``structured_output`` may carry an ``attachments`` list.
  * Each attachment is a dict. ``kind`` selects the transport shape:
    ``"image"`` (a ``url`` or ``mime_type`` + base64 ``data``) and
    ``"audio"`` (base64 ``data`` + a ``format`` tag) are recognized;
    further values layer on the same protocol without breaking callers.
  * The runtime moves recognized attachments off ``structured_output``
    into ``ChatMessage.metadata["attachments"]`` so the textual
    part stays JSON-serializable for the provider's flat content
    field, and the attachment list is available for wire-time
    unpacking by provider-aware code.

At wire time the provider ``_payload()`` inspects the metadata and,
when attachments are present, emits the OpenAI-native ``content``
list shape::

    content = [
        {"type": "text", "text": "<json summary>"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ]

Providers that don't natively accept tool-role attachments (e.g.
Anthropic today — followup work) silently ignore the metadata and
still see the textual ``content`` part, so behaviour degrades
gracefully.

Public surface:

* :func:`extract_attachments_from_structured_output` — pure helper
  that pops the ``attachments`` list off a structured-output dict and
  returns ``(remaining_structured, attachments_list)``. Used by
  ``tool_stage`` when building tool ChatMessages.
* :func:`normalize_attachment` — single-attachment validator: type-
  checks, base64-roundtrips ``data`` to surface corrupt inputs early,
  returns a canonical dict shape.
* :func:`build_openai_tool_content_list` — assembles the
  ``content`` list shape OpenAI-compat expects when a tool message
  has attachments. Returns ``None`` when there are no recognized
  attachments so callers can keep their flat-string codepath.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

_RECOGNIZED_KINDS = frozenset({"image", "audio"})
_DEFAULT_KIND = "image"  # legacy callers that omit ``kind`` get image.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB — provider limits vary.
# OpenAI ``input_audio`` formats (no URL form — base64 ``data`` + ``format``).
_AUDIO_FORMATS = frozenset({"wav", "mp3"})


def _b64_prefix_ok(data: str) -> bool:
    """Quick base64 sanity-check — decode header bytes to catch obvious
    corruption without paying for the full decode of large payloads."""
    sample = data[: min(128, len(data))]
    try:
        # validate=True rejects whitespace + non-base64 chars; the prefix
        # is a valid base64 substring iff its length is a multiple of 4
        # (or padded). We pad it ourselves so the validator just checks
        # the alphabet.
        padded = sample + "=" * (-len(sample) % 4)
        base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def normalize_attachment(raw: Any) -> dict[str, Any] | None:
    """Validate and canonicalize a single attachment entry.

    Returns a canonical dict on success, or ``None`` when the entry is
    malformed (caller drops it silently and keeps siblings). The shape
    depends on ``kind``:

      * image: ``{"kind": "image", "url": str}`` (http(s)/data URL) or
        ``{"kind": "image", "mime_type": str, "data": str}`` (base64).
      * audio: ``{"kind": "audio", "data": str, "format": str}``
        (base64 + an OpenAI ``input_audio`` format).
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind", _DEFAULT_KIND)
    if not isinstance(kind, str) or kind not in _RECOGNIZED_KINDS:
        return None
    if kind == "audio":
        return _normalize_audio(raw)
    return _normalize_image(raw)


def _normalize_image(raw: dict[str, Any]) -> dict[str, Any] | None:
    # URL-referenced attachment (e.g. a user-supplied image_url). Passed
    # through to the provider as-is; no base64 round-trip. Accept http(s)
    # and data: URLs.
    url = raw.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://", "data:")):
        return {"kind": "image", "url": url}
    mime_type = raw.get("mime_type")
    data = raw.get("data")
    if not isinstance(mime_type, str) or "/" not in mime_type:
        return None
    if not isinstance(data, str) or not data:
        return None
    if not _b64_prefix_ok(data):
        return None
    if len(data) > _MAX_ATTACHMENT_BYTES:
        # Don't crash the run for an oversized image — drop with a
        # diagnostic marker the caller can log. Returning None signals
        # "skip this entry".
        return None
    return {"kind": "image", "mime_type": mime_type, "data": data}


def _normalize_audio(raw: dict[str, Any]) -> dict[str, Any] | None:
    # OpenAI ``input_audio`` carries base64 ``data`` + a ``format`` tag
    # ("wav"/"mp3"); there is no URL form. Unknown formats are dropped so
    # a backend never 400s on an unsupported tag.
    data = raw.get("data")
    fmt = raw.get("format")
    if not isinstance(data, str) or not data:
        return None
    if not isinstance(fmt, str) or fmt.lower() not in _AUDIO_FORMATS:
        return None
    if not _b64_prefix_ok(data):
        return None
    if len(data) > _MAX_ATTACHMENT_BYTES:
        return None
    return {"kind": "audio", "data": data, "format": fmt.lower()}


def extract_attachments_from_structured_output(
    structured: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    """Split attachments off a tool envelope's ``structured_output``.

    Returns ``(remaining_structured, normalized_attachments)``. The
    remaining dict is a shallow copy with the ``attachments`` key
    removed when at least one valid entry was found; otherwise the
    original input is returned unchanged.

    Non-dict ``structured`` returns unchanged with an empty list.
    """
    if not isinstance(structured, dict):
        return structured, []
    raw_list = structured.get("attachments")
    if not isinstance(raw_list, list) or not raw_list:
        return structured, []
    normalized: list[dict[str, Any]] = []
    for entry in raw_list:
        candidate = normalize_attachment(entry)
        if candidate is not None:
            normalized.append(candidate)
    if not normalized:
        # Bag was non-empty but every entry was malformed — keep the
        # original ``structured`` untouched so callers see the
        # diagnostic noise (e.g. typo'd mime_type) instead of a
        # mysteriously-empty attachments list.
        return structured, []
    remaining = {k: v for k, v in structured.items() if k != "attachments"}
    return remaining, normalized


def build_openai_tool_content_list(
    text_content: str,
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Build the OpenAI-compat ``content`` list for a tool message.

    Returns the ``[{type=text,...}, {type=image_url,...}, ...]`` shape
    when at least one attachment is present, or ``None`` when the
    attachments list is empty (callers keep the flat-string codepath).

    Image attachments project to ``{"type": "image_url", "image_url":
    {"url": "data:<mime>;base64,<data>"}}``; audio attachments project to
    ``{"type": "input_audio", "input_audio": {"data": <base64>, "format":
    <fmt>}}``. Unknown kinds are dropped with no error so adding a new
    transport doesn't break older code paths.
    """
    if not attachments:
        return None
    blocks: list[dict[str, Any]] = []
    if text_content:
        blocks.append({"type": "text", "text": text_content})
    for attachment in attachments:
        kind = attachment.get("kind")
        if kind == "image":
            url = attachment.get("url")
            if isinstance(url, str) and url:
                image_url = url
            else:
                mime = attachment["mime_type"]
                data = attachment["data"]
                image_url = f"data:{mime};base64,{data}"
            blocks.append({"type": "image_url", "image_url": {"url": image_url}})
        elif kind == "audio":
            blocks.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": attachment["data"],
                        "format": attachment["format"],
                    },
                }
            )
        # Future ``kind`` values silently dropped here so adding a new
        # transport doesn't require touching this function.
    if not blocks:
        return None
    # If only attachments rendered (text was empty), ensure at least
    # one text block exists — some OpenAI-compat backends 400 on a
    # content list of purely non-text blocks.
    if not any(b.get("type") == "text" for b in blocks):
        blocks.insert(0, {"type": "text", "text": ""})
    return blocks


__all__ = [
    "normalize_attachment",
    "extract_attachments_from_structured_output",
    "build_openai_tool_content_list",
]
