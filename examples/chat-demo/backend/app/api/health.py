"""Health endpoint."""

from __future__ import annotations

from app.deps import get_agent_bundle
from app.observability import tracing_status
from app.schemas.meta import HealthResponse, ProviderStatusView
from app.services.agent_factory import AgentBundle
from fastapi import APIRouter, Depends

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
async def health(bundle: AgentBundle = Depends(get_agent_bundle)) -> HealthResponse:
    """Return runtime and provider status."""
    status = await bundle.agent.runner.deps.provider.healthcheck()
    return HealthResponse(
        ok=True,
        store_kind=bundle.store_kind,
        provider=ProviderStatusView(
            provider_name=status.provider_name,
            provider_kind=status.provider_kind.value,
            healthy=status.healthy,
            configured=status.configured,
            latency_ms=status.latency_ms,
            avg_latency_ms=status.avg_latency_ms,
            request_count=status.request_count,
            error_count=status.error_count,
        ),
        tracing=tracing_status(),
    )
