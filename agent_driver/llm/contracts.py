"""Provider-neutral LLM contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
)


class LlmFinishReason(str, Enum):
    """Normalized completion finish reason."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    ERROR = "error"
    UNKNOWN = "unknown"


class LlmProviderKind(str, Enum):
    """Provider implementation kind."""

    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"


class RouterStrategy(str, Enum):
    """Provider selection strategy for router."""

    LATENCY = "latency"
    COST = "cost"
    BALANCED = "balanced"


class LlmRequest(ContractModel):
    """Provider-neutral completion request."""

    messages: list[ChatMessage] = Field(default_factory=list)
    model_role: str = "default"
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, value: int | None) -> int | None:
        """Validate non-negative max token bound."""
        if value is None:
            return value
        if value <= 0:
            raise ValueError("max_tokens must be > 0")
        return value

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: float | None) -> float | None:
        """Validate non-negative temperature value."""
        return ensure_non_negative_float(value, field_name="temperature")

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure tool payloads stay JSON-compatible for provider transport."""
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("tools must contain object entries")
            normalized.append(
                ensure_json_serializable(item, field_name="tools payload")
            )
        return normalized

    @field_validator("tool_choice")
    @classmethod
    def validate_tool_choice(
        cls, value: str | dict[str, Any] | None
    ) -> str | dict[str, Any] | None:
        """Validate provider-neutral tool choice payload."""
        if value is None:
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return ensure_json_serializable(value, field_name="tool_choice payload")
        raise ValueError("tool_choice must be string, object, or null")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class LlmResponse(ContractModel):
    """Provider-neutral completion response."""

    message: ChatMessage
    finish_reason: LlmFinishReason = LlmFinishReason.UNKNOWN
    usage: UsageSummary = Field(default_factory=UsageSummary)
    provider: str
    model: str
    raw_response: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_response", "metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure raw response and metadata are JSON-compatible."""
        return ensure_json_serializable(value, field_name="llm response payload")


class LlmStreamEvent(ContractModel):
    """Normalized LLM stream chunk event."""

    event: str
    delta_text: str = ""
    # Vendor-specific "thinking" channel (vLLM ``delta.reasoning_content``
    # for Qwen3 enable_thinking, DeepSeek-R1, …). Streamed in parallel
    # with ``delta_text`` — empty for providers that don't emit reasoning.
    delta_reasoning: str = ""
    finish_reason: LlmFinishReason | None = None
    usage: UsageSummary | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="stream metadata")


class ProviderStatus(ContractModel):
    """Current health and telemetry for a provider instance."""

    provider_name: str
    provider_kind: LlmProviderKind
    healthy: bool = True
    configured: bool = True
    latency_ms: float | None = None
    avg_latency_ms: float | None = None
    request_count: int = 0
    error_count: int = 0
    cost_per_1k_tokens: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latency_ms", "avg_latency_ms", "cost_per_1k_tokens")
    @classmethod
    def validate_non_negative_floats(cls, value: float | None) -> float | None:
        """Validate non-negative floating point telemetry metrics."""
        return ensure_non_negative_float(value, field_name="provider float metric")

    @field_validator("request_count", "error_count")
    @classmethod
    def validate_non_negative_ints(cls, value: int) -> int:
        """Validate non-negative request/error counters."""
        return int(ensure_non_negative_int(value, field_name="provider counter"))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="provider metadata")

    @model_validator(mode="after")
    def fill_average_latency(self) -> "ProviderStatus":
        """Initialize average latency from latest measurement when missing."""
        if self.avg_latency_ms is None and self.latency_ms is not None:
            self.avg_latency_ms = self.latency_ms
        return self

    @property
    def error_rate(self) -> float:
        """Return provider error rate in [0, 1]."""
        if self.request_count <= 0:
            return 0.0
        return self.error_count / self.request_count
