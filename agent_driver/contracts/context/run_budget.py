"""Typed run-scoped context budget contracts for host embedders."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable

MAX_RUN_CONTEXT_TOKENS = 2_000_000
MAX_RUN_CONTEXT_ITEMS = 4096
MAX_RUN_PREVIEW_CHARS = 8192
MAX_RUN_COMPACTION_CHARS = 262_144

StrictPositiveTokens = Annotated[
    int, Field(strict=True, ge=1, le=MAX_RUN_CONTEXT_TOKENS)
]
StrictNonNegativeTokens = Annotated[
    int, Field(strict=True, ge=0, le=MAX_RUN_CONTEXT_TOKENS)
]
StrictContextItems = Annotated[
    int, Field(strict=True, ge=0, le=MAX_RUN_CONTEXT_ITEMS)
]


class RunContextBudget(ContractModel):
    """Caller-owned semantic budget for one agent run.

    The token window is the only required input. Optional semantic caps let a
    host tighten message/observation retention without reaching into runner
    internals. Omitted caps scale deterministically from the runner defaults.
    """

    input_tokens: StrictPositiveTokens
    output_tokens: StrictNonNegativeTokens = 0
    max_messages: StrictContextItems | None = None
    max_observations: StrictContextItems | None = None
    protect_recent_messages: StrictContextItems | None = None
    preserve_recent_observations: StrictContextItems | None = None
    max_observation_preview_chars: Annotated[
        int, Field(strict=True, ge=0, le=MAX_RUN_PREVIEW_CHARS)
    ] | None = None
    max_compaction_chars: Annotated[
        int, Field(strict=True, ge=1, le=MAX_RUN_COMPACTION_CHARS)
    ] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Keep host policy metadata transport-safe."""
        return ensure_json_serializable(
            value, field_name="run context budget metadata"
        )


class ContextBudgetDefaults(ContractModel):
    """Typed defaults supplied to :func:`resolve_run_context_budget`."""

    max_chars: int
    max_messages: int | None = None
    max_observations: int | None = None
    protect_recent_messages: int | None = None
    preserve_recent_observations: int | None = None
    max_observation_preview_chars: int | None = None
    context_window_estimate: int
    warning_threshold: int
    compact_threshold: int
    blocking_threshold: int
    output_token_reserve: int
    max_compaction_chars: int = 4000
    source: str = "runner_config"


class ResolvedRunContextBudget(ContractModel):
    """Effective request, pressure, and compaction limits with safe audit."""

    source: str
    input_tokens: int
    output_tokens: int
    max_chars: int
    max_messages: int | None = None
    max_observations: int | None = None
    protect_recent_messages: int | None = None
    preserve_recent_observations: int | None = None
    max_observation_preview_chars: int | None = None
    context_window_estimate: int
    warning_threshold: int
    compact_threshold: int
    blocking_threshold: int
    output_token_reserve: int
    max_compaction_chars: int
    audit: dict[str, Any] = Field(default_factory=dict)

    @field_validator("audit")
    @classmethod
    def validate_audit(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure the size/strategy audit is JSON-safe."""
        return ensure_json_serializable(value, field_name="context budget audit")


__all__ = [
    "ContextBudgetDefaults",
    "ResolvedRunContextBudget",
    "RunContextBudget",
]
