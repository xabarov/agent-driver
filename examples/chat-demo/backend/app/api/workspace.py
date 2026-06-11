"""Session workspace endpoints for chat demo."""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.deps import get_settings
from app.schemas.meta import (
    WorkspaceArtifactPreviewResponse,
    WorkspaceArtifactsResponse,
    WorkspaceArtifactView,
    WorkspaceImportResponse,
)
from app.workspace import (
    import_sample_project,
    list_workspace_artifacts,
    preview_workspace_artifact,
    read_workspace_artifact,
    render_markdown_artifact_pdf,
    workspace_status,
)

router = APIRouter(tags=["workspace"])


@router.post("/workspace/sample", response_model=WorkspaceImportResponse)
async def import_workspace_sample(session_id: str) -> WorkspaceImportResponse:
    """Import a tiny sample project into one session workspace."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    settings = get_settings()
    try:
        files = import_sample_project(settings, clean_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceImportResponse(
        files=files,
        workspace=workspace_status(settings, clean_session_id, create=True),
    )


@router.get(
    "/workspace/{session_id}/artifacts",
    response_model=WorkspaceArtifactsResponse,
)
async def workspace_artifacts(session_id: str) -> WorkspaceArtifactsResponse:
    """Return session artifact index."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    settings = get_settings()
    try:
        artifact_rows = list_workspace_artifacts(settings, clean_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    artifacts = [
        WorkspaceArtifactView(
            path=item.path,
            kind=item.kind,
            sizeBytes=item.size_bytes,
            modifiedAt=item.modified_at,
        )
        for item in artifact_rows
    ]
    return WorkspaceArtifactsResponse(sessionId=clean_session_id, artifacts=artifacts)


@router.get("/workspace/{session_id}/artifacts/{artifact_path:path}/download")
async def workspace_artifact_download(
    session_id: str,
    artifact_path: str,
) -> Response:
    """Download raw Markdown/JSONL artifact bytes from the session workspace."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        artifact, content = read_workspace_artifact(
            get_settings(),
            clean_session_id,
            artifact_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = _safe_download_filename(PurePosixPath(artifact.path).name)
    media_type = (
        "text/markdown; charset=utf-8" if filename.endswith(".md") else "text/plain"
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/workspace/{session_id}/artifacts/{artifact_path:path}/download.pdf")
async def workspace_artifact_pdf_download(
    session_id: str,
    artifact_path: str,
) -> Response:
    """Download a text-only PDF rendering of a Markdown artifact."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        artifact, content = read_workspace_artifact(
            get_settings(),
            clean_session_id,
            artifact_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not artifact.path.endswith(".md"):
        raise HTTPException(
            status_code=400, detail="PDF export supports Markdown artifacts"
        )
    filename = _safe_download_filename(
        f"{PurePosixPath(artifact.path).stem or 'artifact'}.pdf"
    )
    pdf = render_markdown_artifact_pdf(
        content,
        title=PurePosixPath(artifact.path).name,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_download_filename(filename: str) -> str:
    unsafe_chars = {'"', "\\", "\r", "\n"}
    cleaned = "".join(
        char if char not in unsafe_chars else "_" for char in filename.strip()
    )
    return cleaned or "artifact.txt"


@router.get(
    "/workspace/{session_id}/artifacts/{artifact_path:path}",
    response_model=WorkspaceArtifactPreviewResponse,
)
async def workspace_artifact_preview(
    session_id: str,
    artifact_path: str,
) -> WorkspaceArtifactPreviewResponse:
    """Return bounded text preview for one session artifact."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        artifact, content, truncated = preview_workspace_artifact(
            get_settings(),
            clean_session_id,
            artifact_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceArtifactPreviewResponse(
        sessionId=clean_session_id,
        path=artifact.path,
        kind=artifact.kind,
        sizeBytes=artifact.size_bytes,
        content=content,
        truncated=truncated,
    )
