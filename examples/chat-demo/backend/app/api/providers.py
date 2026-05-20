"""Provider metadata endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_agent_bundle, get_settings
from app.schemas.meta import ProviderResponse, ProviderStatusView
from app.services.agent_factory import AgentBundle

router = APIRouter(tags=["meta"])


@router.get("/providers", response_model=ProviderResponse)
async def providers(bundle: AgentBundle = Depends(get_agent_bundle)) -> ProviderResponse:
    """Return active provider and normalized status."""
    status = await bundle.agent.runner.deps.provider.healthcheck()
    settings = get_settings()
    return ProviderResponse(
        name=bundle.agent.runner.deps.provider.name,
        model=settings.model,
        base_url=settings.base_url,
        status=ProviderStatusView(
            provider_name=status.provider_name,
            provider_kind=status.provider_kind.value,
            healthy=status.healthy,
            configured=status.configured,
            latency_ms=status.latency_ms,
            avg_latency_ms=status.avg_latency_ms,
            request_count=status.request_count,
            error_count=status.error_count,
        ),
    )

