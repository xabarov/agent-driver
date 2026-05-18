"""Usage and cost telemetry contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
)


class UsageSummary(ContractModel):
    """Normalized usage, cache, and cost telemetry for one run segment."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd_estimate: float | None = None
    model_provider: str | None = None
    model_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    )
    @classmethod
    def validate_non_negative_ints(cls, value: int | None) -> int | None:
        """Validate non-negative token counters."""
        return ensure_non_negative_int(value, field_name="token counter")

    @field_validator("cost_usd_estimate")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        """Validate non-negative cost estimate."""
        return ensure_non_negative_float(value, field_name="cost_usd_estimate")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @model_validator(mode="after")
    def reconcile_total_tokens(self) -> "UsageSummary":
        """Auto-fill total tokens when input/output are present."""
        if self.total_tokens == 0 and (self.input_tokens > 0 or self.output_tokens > 0):
            self.total_tokens = self.input_tokens + self.output_tokens
        return self
