"""Session workspace endpoints for chat demo."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import get_settings
from app.schemas.meta import WorkspaceImportResponse
from app.workspace import import_sample_project, workspace_status

router = APIRouter(tags=["workspace"])


@router.post("/workspace/sample", response_model=WorkspaceImportResponse)
def import_workspace_sample(session_id: str) -> WorkspaceImportResponse:
    """Import a tiny sample project into one session workspace."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    settings = get_settings()
    files = import_sample_project(settings, clean_session_id)
    return WorkspaceImportResponse(
        files=files,
        workspace=workspace_status(settings, clean_session_id, create=True),
    )
