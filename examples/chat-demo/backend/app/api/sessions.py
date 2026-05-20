"""Session CRUD endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.chat import replay_events_for_run
from app.deps import get_agent_bundle
from app.schemas.chat import ReplayResponse
from app.schemas.sessions import (
    CreateSessionRequest,
    DeleteSessionResponse,
    SessionDetailView,
    SessionMessageView,
    SessionsListResponse,
    SessionSummaryView,
)
from app.services.agent_factory import AgentBundle

router = APIRouter(tags=["sessions"])


def _title_for_transcript(session_id: str, transcript: tuple[tuple[str, str], ...]) -> str:
    for role, text in transcript:
        if role == "user" and text.strip():
            return text.strip()[:40]
    return session_id


def _detail_from_record(record) -> SessionDetailView:
    transcript = [
        SessionMessageView(role=role, content=text) for role, text in record.transcript
    ]
    return SessionDetailView(
        session_id=record.session_id,
        thread_id=record.thread_id,
        title=_title_for_transcript(record.session_id, record.transcript),
        run_ids=list(record.run_ids),
        transcript=transcript,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _delete_session_file(path: Path, session_id: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    rows = payload.get("sessions")
    if not isinstance(rows, list):
        return False
    filtered = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("session_id")) != session_id
    ]
    if len(filtered) == len(rows):
        return False
    payload["sessions"] = filtered
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return True


@router.get("/sessions", response_model=SessionsListResponse)
def list_sessions(bundle: AgentBundle = Depends(get_agent_bundle)) -> SessionsListResponse:
    """Return all persisted sessions sorted by updated_at descending."""
    records = bundle.session_store.list_sessions()
    rows = [
        SessionSummaryView(
            session_id=record.session_id,
            thread_id=record.thread_id,
            title=_title_for_transcript(record.session_id, record.transcript),
            updated_at=record.updated_at,
            runs_count=len(record.run_ids),
        )
        for record in records
    ]
    rows.sort(key=lambda item: item.updated_at, reverse=True)
    return SessionsListResponse(sessions=rows)


@router.post("/sessions", response_model=SessionDetailView)
def create_session(
    body: CreateSessionRequest,
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> SessionDetailView:
    """Create an empty session row."""
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    thread_id = f"thread_{uuid.uuid4().hex[:8]}"
    transcript: list[tuple[str, str]] = []
    if body.title and body.title.strip():
        now = datetime.now(UTC).isoformat()
        transcript.append(("system", f"title:{body.title.strip()} [{now}]"))
    record = bundle.session_store.upsert(
        session_id=session_id,
        thread_id=thread_id,
        run_ids=[],
        transcript=transcript,
    )
    return _detail_from_record(record)


@router.get("/sessions/{session_id}", response_model=SessionDetailView)
def get_session(
    session_id: str, bundle: AgentBundle = Depends(get_agent_bundle)
) -> SessionDetailView:
    """Return one session by identifier."""
    record = bundle.session_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _detail_from_record(record)


@router.get("/sessions/{session_id}/replay", response_model=ReplayResponse)
def replay_session_run(
    session_id: str,
    run_id: str = Query(..., alias="run_id"),
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> ReplayResponse:
    """Return persisted stream events for one run in a session."""
    record = bundle.session_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    if run_id not in record.run_ids:
        raise HTTPException(status_code=404, detail="run not found in session")
    return ReplayResponse(
        run_id=run_id,
        events=replay_events_for_run(bundle, run_id),
    )


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
def delete_session(
    session_id: str, bundle: AgentBundle = Depends(get_agent_bundle)
) -> DeleteSessionResponse:
    """Delete one session record from JSON store."""
    deleted = _delete_session_file(bundle.session_store.path, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return DeleteSessionResponse(ok=True)

