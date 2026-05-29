"""Provider protocol interfaces for LLM adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from agent_driver.llm.contracts import (
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)


class LlmProvider(Protocol):
    """Provider protocol for complete/stream and health telemetry."""

    @property
    def name(self) -> str:
        """Stable provider instance name."""

    @property
    def status(self) -> ProviderStatus:
        """Current provider status snapshot."""

    async def healthcheck(self) -> ProviderStatus:
        """Update and return provider status."""

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Run non-streaming completion request."""

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Run streaming completion request."""
