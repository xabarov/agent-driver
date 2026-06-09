"""Agent profile and prompt template contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import AgentProfile
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_positive_int,
)


class PromptTemplate(ContractModel):
    """Versioned prompt template metadata and render payload."""

    template_id: str
    version: int = 1
    profile: AgentProfile
    required_placeholders: list[str] = Field(default_factory=list)
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """Validate strictly positive semantic version."""
        validated = ensure_positive_int(value, field_name="version")
        assert validated is not None
        return validated

    @field_validator("required_placeholders", mode="after")
    @classmethod
    def normalize_placeholders(cls, value: list[str]) -> list[str]:
        """Normalize placeholders and ensure deterministic ordering."""
        normalized = sorted({item.strip() for item in value if item.strip()})
        return normalized

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="metadata")


class PromptRenderResult(ContractModel):
    """Rendered prompt payload with deterministic hash metadata."""

    template_id: str
    template_version: int
    profile: AgentProfile
    rendered_text: str
    rendered_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template_version")
    @classmethod
    def validate_template_version(cls, value: int) -> int:
        """Validate strictly positive template version."""
        validated = ensure_positive_int(value, field_name="template_version")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="metadata")


class HarnessProfile(ContractModel):
    """Declarative per-provider/model harness shaping, applied at assembly.

    A profile bends the request for the models it matches without editing the
    step loop or prompt templates: it wraps the assembled system prompt in
    ``system_prefix`` / ``system_suffix`` slots (the BASE/SUFFIX around the
    USER-assembled core), drops ``excluded_tools`` from the model-visible
    catalog, and rewrites tool descriptions via ``tool_description_overrides``.

    ``match_models`` is a tuple of ``fnmatch`` globs against the request's
    resolved model id; empty matches **any** model (a provider-wide default).
    Selection is first-match over an ordered profile set.
    """

    name: str = Field(..., min_length=1)
    match_models: tuple[str, ...] = ()
    system_prefix: str = ""
    system_suffix: str = ""
    excluded_tools: tuple[str, ...] = ()
    tool_description_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("match_models", "excluded_tools", mode="after")
    @classmethod
    def normalize_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Drop blank entries and de-dupe while preserving order."""
        seen: dict[str, None] = {}
        for item in value:
            stripped = item.strip()
            if stripped:
                seen.setdefault(stripped, None)
        return tuple(seen)

    @field_validator("tool_description_overrides")
    @classmethod
    def validate_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        """Ensure overrides are a JSON-serializable name->description map."""
        return ensure_json_serializable(
            value, field_name="tool_description_overrides"
        )
