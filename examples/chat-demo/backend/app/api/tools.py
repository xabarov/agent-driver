"""Tool-surface endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import ToolPreset
from app.deps import get_agent_bundle_for_preset, resolve_tool_preset
from app.schemas.meta import ToolManifestView, ToolsResponse

router = APIRouter(tags=["meta"])


@router.get("/tools", response_model=ToolsResponse)
def tools(preset: ToolPreset | None = None) -> ToolsResponse:
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
    ]
    return ToolsResponse(tools=payload)
