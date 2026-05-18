"""Deterministic fake LLM provider for offline tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.base import ProviderBase
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)
from agent_driver.llm.streaming import stream_text_chunks


class FakeProvider(ProviderBase):
    """Offline deterministic provider returning predictable responses."""

    def __init__(
        self,
        *,
        name: str = "fake",
        response_text: str = "fake response",
        configured: bool = True,
    ) -> None:
        super().__init__(
            config=ProviderBase.Config(
                name=name,
                kind=LlmProviderKind.FAKE,
                configured=configured,
                cost_per_1k_tokens=0.0,
            )
        )
        self._response_text = response_text
        self.status.latency_ms = 1.0
        self.status.avg_latency_ms = 1.0

    async def healthcheck(self) -> ProviderStatus:
        """Return deterministic healthy status."""
        return self.status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Return deterministic completion response."""
        self._mark_attempt()
        started = self._started_at()
        prompt_chars = sum(len(message.content) for message in request.messages)
        output_tokens = max(1, len(self._response_text) // 4)
        usage = UsageSummary(
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=output_tokens,
            total_tokens=max(1, prompt_chars // 4) + output_tokens,
            model_provider=self.name,
            model_name=request.model or "fake-model",
        )
        self._mark_success(started_at=started)
        response_metadata = {"provider_kind": LlmProviderKind.FAKE.value}
        response_metadata.update(request.metadata)
        return LlmResponse(
            message=ChatMessage(role="assistant", content=self._response_text),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider=self.name,
            model=request.model or "fake-model",
            metadata=response_metadata,
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Return deterministic stream based on fixed response text."""
        self._mark_attempt()
        started = self._started_at()
        async for event in stream_text_chunks(self._response_text):
            if event.finish_reason is not None and event.usage is not None:
                event.usage.model_provider = self.name
                event.usage.model_name = request.model or "fake-model"
            yield event
        self._mark_success(started_at=started)
