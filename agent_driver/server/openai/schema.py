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
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None

    def text_content(self) -> str:
        """Return the message content flattened to plain text."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = [
                str(part.get("text", ""))
                for part in self.content
                if isinstance(part, dict) and part.get("type") in (None, "text")
            ]
            return "".join(parts)
        return ""


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI ``/v1/chat/completions`` request body we honor."""

    model: str
    messages: list[ChatMessageIn] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    # Tolerate (and ignore) any other OpenAI fields a client may send.
    model_config = {"extra": "ignore"}


__all__ = ["ChatCompletionRequest", "ChatMessageIn"]
