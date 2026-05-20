"""Schemas for chat message streaming endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    """Input payload for starting one streamed chat run."""

    session_id: str | None = None
    message: str = Field(min_length=1)

