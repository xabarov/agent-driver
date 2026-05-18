"""Health-aware provider router with fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from agent_driver.llm.contracts import (
    LlmRequest,
    LlmResponse,
    ProviderStatus,
    RouterStrategy,
)
from agent_driver.llm.providers import LlmProvider


@dataclass
class _ScoredProvider:
    provider: LlmProvider
    score: float


class HealthAwareRouter:
    """Select provider by health and strategy, then fallback on failure."""

    class ProviderExecutionError(RuntimeError):
        """Raised when one provider attempt fails during completion."""

    def __init__(
        self,
        providers: list[LlmProvider],
        *,
        strategy: RouterStrategy = RouterStrategy.BALANCED,
        fallback_enabled: bool = True,
    ) -> None:
        self._providers = list(providers)
        self._strategy = strategy
        self._fallback_enabled = fallback_enabled

    @property
    def providers(self) -> list[LlmProvider]:
        """Return configured providers list."""
        return self._providers

    @property
    def strategy(self) -> RouterStrategy:
        """Return current provider selection strategy."""
        return self._strategy

    async def refresh_health(self) -> list[ProviderStatus]:
        """Refresh status by invoking provider health checks."""
        statuses: list[ProviderStatus] = []
        for provider in self._providers:
            statuses.append(await provider.healthcheck())
        return statuses

    def _score(self, status: ProviderStatus) -> float:
        if not status.configured or not status.healthy:
            return float("inf")
        latency_score = float(status.avg_latency_ms or status.latency_ms or 9_999.0)
        cost_score = float(status.cost_per_1k_tokens or 0.0) * 1_000.0
        error_penalty = status.error_rate * 10_000.0
        if self._strategy == RouterStrategy.LATENCY:
            return latency_score + error_penalty
        if self._strategy == RouterStrategy.COST:
            return cost_score + error_penalty
        return (latency_score * 0.6) + (cost_score * 0.4) + error_penalty

    def _ranked_candidates(
        self, *, exclude_names: set[str] | None = None
    ) -> list[_ScoredProvider]:
        exclude = exclude_names or set()
        ranked: list[_ScoredProvider] = []
        for provider in self._providers:
            if provider.name in exclude:
                continue
            ranked.append(
                _ScoredProvider(provider=provider, score=self._score(provider.status))
            )
        ranked.sort(key=lambda item: item.score)
        return [item for item in ranked if item.score != float("inf")]

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Execute completion using best provider with optional fallback."""
        await self.refresh_health()
        tried: set[str] = set()
        last_error: HealthAwareRouter.ProviderExecutionError | None = None
        while True:
            candidates = self._ranked_candidates(exclude_names=tried)
            if not candidates:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("No healthy/configured providers available")
            selected = candidates[0].provider
            started = monotonic()
            try:
                response = await selected.complete(request)
                self.record_result(
                    selected, success=True, elapsed_ms=(monotonic() - started) * 1000
                )
                return response
            except (RuntimeError, ValueError) as exc:
                self.record_result(
                    selected, success=False, elapsed_ms=(monotonic() - started) * 1000
                )
                last_error = self.ProviderExecutionError(
                    f"Provider '{selected.name}' failed"
                )
                last_error.__cause__ = exc
                tried.add(selected.name)
                if not self._fallback_enabled:
                    raise

    def record_result(
        self, provider: LlmProvider, *, success: bool, elapsed_ms: float
    ) -> None:
        """Update provider status metrics after a request attempt."""
        status = provider.status
        if not success:
            status.healthy = False
        if status.avg_latency_ms is None:
            status.avg_latency_ms = elapsed_ms
        else:
            status.avg_latency_ms = (status.avg_latency_ms * 0.7) + (elapsed_ms * 0.3)
        status.latency_ms = elapsed_ms
