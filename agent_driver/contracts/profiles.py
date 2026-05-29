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
