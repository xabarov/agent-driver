"""Session API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class SessionMessageView(BaseModel):
    """One transcript entry."""

    role: str
    content: str
    metadata: dict[str, object] | None = None


class SessionSummaryView(BaseModel):
    """Compact session list row."""

    session_id: str
    thread_id: str
    title: str
    updated_at: str
    runs_count: int


class SessionDetailView(BaseModel):
    """Full session payload."""

    session_id: str
    thread_id: str
    title: str
    run_ids: list[str]
    transcript: list[SessionMessageView]
    metadata_by_run: dict[str, dict[str, object]] = {}
    created_at: str
    updated_at: str


class SessionsListResponse(BaseModel):
    """Session list response."""

    sessions: list[SessionSummaryView]


class CreateSessionRequest(BaseModel):
    """Session creation input."""

    title: str | None = None


class DeleteSessionResponse(BaseModel):
    """Delete response envelope."""

    ok: bool

