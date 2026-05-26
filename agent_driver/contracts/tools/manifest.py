"""Tool manifest contracts."""

from __future__ import annotations

import keyword
import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_positive_int,
)


class ToolManifest(ContractModel):
    """Model-facing metadata for one registered tool."""

    name: str
    description: str
    risk: ToolRisk = ToolRisk.LOW
    side_effect: SideEffectClass = SideEffectClass.NONE
    approval_mode: ApprovalMode = ApprovalMode.NEVER
    timeout_seconds: float | None = 30.0
    output_char_budget: int | None = 4000
    idempotent: bool = True
    # Phase 11 H12 — whether this tool may run concurrently with other
    # ``concurrency_safe=True`` tools in the same planned batch. When
    # ``None`` (default), the executor derives the value from
    # ``idempotent`` + ``side_effect`` via ``is_concurrency_safe()``.
    # Set explicitly when the derived default would be wrong (e.g. an
    # idempotent network read whose remote rate-limits forbid parallel
    # calls — declare ``concurrency_safe=False``).
    concurrency_safe: bool | None = None
    # Phase 11 H17 — per-tool semantic for "what happens when a new
    # user message arrives mid-execution". When ``None`` (default), the
    # executor derives from ``side_effect``: irreversible / external
    # action → ``"block"`` (queue the new message until tool completes,
    # avoiding mid-write torn state); reversible or no side effect →
    # ``"cancel"`` (drop the in-flight tool result and route the new
    # message immediately). Set explicitly to override.
    interrupt_behavior: Literal["cancel", "block"] | None = None
    args_schema: dict[str, Any] | None = None
    output_type: str | None = None
    output_schema: dict[str, Any] | None = None
    remediation_hints: list[str] = Field(default_factory=list)
    supported_profiles: list[AgentProfile] = Field(
        default_factory=lambda: [
            AgentProfile.TOOL_CALLING,
            AgentProfile.REACT_TEXT,
            AgentProfile.CODE_AGENT,
        ]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_concurrency_safe(self) -> bool:
        """Resolve the effective concurrency-safe flag.

        Phase 11 H12 — when ``concurrency_safe`` is set explicitly, use it.
        Otherwise derive from ``idempotent`` + ``side_effect``: a tool is
        concurrency-safe by default only when it's idempotent AND has no
        observable side effect (``NONE`` or ``READ_ONLY``).

        Any write / external action defaults to ``False`` (executor must
        serialize it) even when ``idempotent=True``.
        """
        if self.concurrency_safe is not None:
            return self.concurrency_safe
        return self.idempotent and self.side_effect in (
            SideEffectClass.NONE,
            SideEffectClass.READ_ONLY,
        )

    def resolved_interrupt_behavior(self) -> Literal["cancel", "block"]:
        """Resolve the effective interrupt behaviour.

        Phase 11 H17 — when ``interrupt_behavior`` is set explicitly,
        use it. Otherwise:

        * IRREVERSIBLE_WRITE / EXTERNAL_ACTION → ``"block"`` (we must
          let the tool complete to avoid mid-write torn state);
        * NONE / READ_ONLY / REVERSIBLE_WRITE → ``"cancel"`` (safe to
          drop the in-flight result and serve the new user message
          immediately).

        Runtime consumers should call this rather than reading the
        raw field so the default-derivation logic stays in one place.
        """
        if self.interrupt_behavior is not None:
            return self.interrupt_behavior
        if self.side_effect in (
            SideEffectClass.IRREVERSIBLE_WRITE,
            SideEffectClass.EXTERNAL_ACTION,
        ):
            return "block"
        return "cancel"

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        """Validate positive timeout when configured."""
        if value is not None and value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value

    @field_validator("output_char_budget")
    @classmethod
    def validate_output_budget(cls, value: int | None) -> int | None:
        """Validate positive output budget when configured."""
        return ensure_positive_int(value, field_name="output_char_budget")

    @field_validator("metadata")
    @classmethod
    def validate_manifest_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure manifest metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="manifest.metadata")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Validate stable tool name with broad compatibility."""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError(
                "tool name must match [A-Za-z0-9_.:-]+ for stable prompt rendering"
            )
        return value

    @field_validator("args_schema", "output_schema")
    @classmethod
    def validate_optional_schemas(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure optional JSON schemas stay serializable."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="manifest.schema")

    @field_validator("supported_profiles", mode="after")
    @classmethod
    def normalize_supported_profiles(
        cls, value: list[AgentProfile]
    ) -> list[AgentProfile]:
        """Ensure stable unique profile list."""
        if not value:
            raise ValueError("supported_profiles must include at least one profile")
        unique: list[AgentProfile] = []
        for profile in value:
            if profile not in unique:
                unique.append(profile)
        return unique

    @model_validator(mode="after")
    def validate_profile_name_compatibility(self) -> "ToolManifest":
        """Ensure tool naming stays compatible with selected profiles."""
        if AgentProfile.CODE_AGENT in self.supported_profiles and (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name)
            or keyword.iskeyword(self.name)
        ):
            raise ValueError(
                "code_agent compatible tools must use a valid Python identifier name"
            )
        return self


__all__ = ["ToolManifest"]
