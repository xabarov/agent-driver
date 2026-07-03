"""Provider metadata endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_agent_bundle, get_settings
from app.schemas.meta import ProviderResponse, ProviderStatusView
from app.services.agent_factory import AgentBundle
from agent_driver.llm.contracts import ProviderStatus

router = APIRouter(tags=["meta"])


@router.get("/providers", response_model=ProviderResponse)
async def providers(bundle: AgentBundle = Depends(get_agent_bundle)) -> ProviderResponse:
    """Return active provider and normalized status."""
    status = await bundle.agent.runner.deps.provider.healthcheck()
    settings = get_settings()
    return ProviderResponse(
        name=bundle.agent.runner.deps.provider.name,
        model=settings.model,
        base_url=None,
        base_url_family=_base_url_family(status),
        capability_profile=_metadata_dict(status, "capability_profile"),
        route_profile=_metadata_dict(status, "route_profile"),
        provider_preflight=_metadata_dict(status, "provider_preflight"),
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


def _metadata_dict(status: ProviderStatus, key: str) -> dict[str, object] | None:
    value = status.metadata.get(key)
    return dict(value) if isinstance(value, dict) else None


def _base_url_family(status: ProviderStatus) -> str | None:
    route_profile = _metadata_dict(status, "route_profile")
    if route_profile is not None:
        value = route_profile.get("base_url_family")
        if isinstance(value, str) and value:
            return value
    preflight = _metadata_dict(status, "provider_preflight")
    if preflight is not None:
        value = preflight.get("base_url_family")
        if isinstance(value, str) and value:
            return value
    return None
