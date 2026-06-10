"""Pydantic models for the OpenAI-compatible ``/v1/chat/completions`` surface.

Only the request side is modelled (for validation); responses are assembled as
plain dicts in :mod:`agent_driver.server.openai.translate` so the exact OpenAI
wire shape stays explicit and easy to diff against the spec.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """One inbound OpenAI chat message.

    ``content`` is a string in the common case, or a list of content parts
    (vision/multimodal); :func:`text_content` flattens the latter to text.
    """

    role: str
    content: str | list[Any] | dict[str, Any] | None = None
    name: str | None = None

    def text_content(self) -> str:
        """Return the message content flattened to plain text.

        Defensive against the shapes real clients send: a plain string, OpenAI's
        list of typed parts (``{"type": "text", "text": ...}``), Open WebUI's
        bare-string-or-part list, or a single content dict. Non-text parts
        (``image_url`` / ``input_image`` / files) are skipped — the agent surface
        is text — rather than raising."""
        return _flatten_text(self.content)


def _flatten_text(content: Any) -> str:
    """Flatten arbitrary OpenAI-ish content into plain text (no exceptions)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                kind = part.get("type")
                if kind in (None, "text", "input_text", "output_text"):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                # image_url / input_image / file parts: skipped (text-only agent).
        return "".join(parts)
    return ""


class StreamOptions(BaseModel):
    """OpenAI ``stream_options`` (only ``include_usage`` is honored)."""

    include_usage: bool = False
    model_config = {"extra": "ignore"}


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI ``/v1/chat/completions`` request body we honor."""

    model: str
    messages: list[ChatMessageIn] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    stream_options: StreamOptions | None = None
    # Tolerate (and ignore) any other OpenAI fields a client may send.
    model_config = {"extra": "ignore"}

    def wants_usage_chunk(self) -> bool:
        """Whether a final usage chunk was requested via stream_options."""
        return bool(self.stream_options and self.stream_options.include_usage)


__all__ = ["ChatCompletionRequest", "ChatMessageIn", "StreamOptions"]
