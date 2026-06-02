"""Schemas for chat message streaming endpoint."""

from __future__ import annotations

from typing import Literal

from app.config import ToolPreset
from pydantic import BaseModel, Field

from agent_driver.contracts import ControlKind, ControlPriority

ResearchMode = Literal["chat", "web", "deep"]
ResearchProfile = Literal["light", "medium", "hard"]
ProfileSource = Literal[
    "user_selected",
    "auto_suggested",
    "backend_classified",
    "scenario_forced",
]


class HardResearchOptions(BaseModel):
    """Explicit opt-ins for high-cost hard research fallbacks."""

    allow_pdf_read: bool = True
    allow_browser_read: bool = False
    allow_browser_action: bool = False


class ChatMessageRequest(BaseModel):
    """Input payload for starting one streamed chat run."""

    session_id: str | None = None
    message: str = Field(min_length=1)
    tool_preset: ToolPreset | None = None
    force_planning: bool | None = None
    model: str | None = None
    retry_from_run_id: str | None = None
    client_request_id: str | None = None
    scenario_id: str | None = None
    research_depth: Literal["deep_parallel_research"] | None = None
    research_mode: ResearchMode | None = None
    research_profile: ResearchProfile | None = None
    profile_source: ProfileSource | None = None
    hard_options: HardResearchOptions | None = None


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


class ChatControlRequest(BaseModel):
    """Steering control payload for a live or resumable chat run."""

    kind: ControlKind
    priority: ControlPriority = ControlPriority.NEXT
    payload: dict[str, object] = Field(default_factory=dict)
    thread_id: str | None = None
    agent_id: str | None = None
    dedupe_key: str | None = None


class ChatControlResponse(BaseModel):
    """Accepted/cancelled steering command response."""

    ok: bool
    control_id: str | None = None
    queue_id: str | None = None
    error: str | None = None
