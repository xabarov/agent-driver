"""Schemas for chat message streaming endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import ToolPreset


class ChatMessageRequest(BaseModel):
    """Input payload for starting one streamed chat run."""

    session_id: str | None = None
    message: str = Field(min_length=1)
    tool_preset: ToolPreset | None = None
    model: str | None = None


class ResumeRequest(BaseModel):
    """Resume payload for a paused run awaiting human input."""

    interrupt_id: str
    action: str
    tool_preset: ToolPreset | None = None
    model: str | None = None
    edited_tool_args: dict[str, object] | None = None
    message: str | None = None


class InterruptView(BaseModel):
    """Pending interrupt details for UI."""

    run_id: str
    interrupt_id: str
    reason: str
    title: str | None = None
    description: str | None = None
    proposed_action: dict[str, object] = Field(default_factory=dict)
    allowed_actions: list[str] = Field(default_factory=list)


class ReplayResponse(BaseModel):
    """Replay payload with normalized stream events."""

    run_id: str
    events: list[dict[str, object]]


class CancelRunResponse(BaseModel):
    """Response for cooperative run cancellation."""

    ok: bool = True
    run_id: str
    cancelled: bool

