"""Tool-surface endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import ToolPreset
from app.deps import get_agent_bundle_for_preset, get_settings, resolve_tool_preset
from app.schemas.meta import ToolManifestView, ToolsResponse
from app.workspace import workspace_status

router = APIRouter(tags=["meta"])

PUBLIC_TOOL_NAMES = {"web_fetch", "web_search"}


@router.get("/tools", response_model=ToolsResponse)
def tools(preset: ToolPreset | None = None, session_id: str | None = None) -> ToolsResponse:
    """List tool manifests for the requested or default preset."""
    bundle = get_agent_bundle_for_preset(resolve_tool_preset(preset))
    payload = [
        ToolManifestView(
            name=manifest.name,
            description=manifest.description,
            risk=manifest.risk.value,
            sideEffect=manifest.side_effect.value,
            approvalMode=manifest.approval_mode.value,
        )
        for manifest in bundle.manifests
        if manifest.name in PUBLIC_TOOL_NAMES
    ]
    return ToolsResponse(
        tools=payload,
        workspace=workspace_status(get_settings(), session_id),
    )
