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

    def image_attachments(self) -> list[dict[str, Any]]:
        """Extract image parts (``image_url`` / ``input_image``) as attachments.

        Returns ``[{"kind": "image", "url": <data: or http(s) url>}]`` for each
        image part; the runtime carries these on ``ChatMessage.metadata`` and the
        provider emits them as ``image_url`` content blocks to a vision model.
        """
        if not isinstance(self.content, list):
            return []
        out: list[dict[str, Any]] = []
        for part in self.content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in ("image_url", "input_image"):
                continue
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if isinstance(url, str) and url:
                out.append({"kind": "image", "url": url})
        return out

    def audio_attachments(self) -> list[dict[str, Any]]:
        """Extract ``input_audio`` parts as attachments.

        Returns ``[{"kind": "audio", "data": <base64>, "format": <fmt>}]`` for
        each audio part; the runtime carries these on ``ChatMessage.metadata``
        and the provider emits them as ``input_audio`` content blocks to an
        audio-capable model.
        """
        if not isinstance(self.content, list):
            return []
        out: list[dict[str, Any]] = []
        for part in self.content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "input_audio":
                continue
            audio = part.get("input_audio")
            if not isinstance(audio, dict):
                continue
            data = audio.get("data")
            fmt = audio.get("format")
            if isinstance(data, str) and data and isinstance(fmt, str) and fmt:
                out.append({"kind": "audio", "data": data, "format": fmt})
        return out

    def media_attachments(self) -> list[dict[str, Any]]:
        """All image + audio attachments carried by this message, in order."""
        return self.image_attachments() + self.audio_attachments()

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


class ResponsesRequest(BaseModel):
    """Subset of the OpenAI ``/v1/responses`` request body we honor.

    ``input`` is either a plain string (a single user turn) or a list of
    message items (``{"role", "content"}``); ``instructions`` is the system
    prompt; ``previous_response_id`` chains onto a stored response.
    """

    model: str = "agent-driver"
    input: str | list[Any] = ""
    instructions: str | None = None
    stream: bool = False
    previous_response_id: str | None = None
    store: bool = True
    temperature: float | None = None
    max_output_tokens: int | None = None
    model_config = {"extra": "ignore"}

    def input_messages(self) -> list[tuple[str, str]]:
        """Return ``(role, text)`` pairs for this request's input.

        A string becomes a single user turn; a list of ``{role, content}``
        items is flattened to text per item (unknown roles fall back to user).
        """
        pairs: list[tuple[str, str]] = []
        if isinstance(self.input, str):
            if self.input.strip():
                pairs.append(("user", self.input))
            return pairs
        for item in self.input:
            if isinstance(item, str):
                pairs.append(("user", item))
            elif isinstance(item, dict):
                role = item.get("role")
                role = role if role in ("system", "user", "assistant") else "user"
                text = _flatten_text(item.get("content"))
                if text:
                    pairs.append((role, text))
        return pairs


__all__ = [
    "ChatCompletionRequest",
    "ChatMessageIn",
    "ResponsesRequest",
    "StreamOptions",
]
