"""Tool-surface endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_agent_bundle
from app.schemas.meta import ToolManifestView, ToolsResponse
from app.services.agent_factory import AgentBundle

router = APIRouter(tags=["meta"])


@router.get("/tools", response_model=ToolsResponse)
def tools(bundle: AgentBundle = Depends(get_agent_bundle)) -> ToolsResponse:
    """List currently selected tool manifests."""
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

