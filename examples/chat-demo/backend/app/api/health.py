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
        jev={
            "rollout": (
                bundle.agent.runner.config.jev_rollout.status()
                if hasattr(bundle.agent.runner.config.jev_rollout, "status")
                else {}
            ),
            "transport": (
                bundle.jev_telemetry.snapshot() if bundle.jev_telemetry is not None else {}
            ),
            "labels": (
                bundle.jev_label_ledger.snapshot()
                if bundle.jev_label_ledger is not None else {}
            ),
        },
    )
