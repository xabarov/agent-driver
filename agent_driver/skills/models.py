"""Contracts for portable Agent Skills."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable


class SkillManifest(ContractModel):
    """Metadata-first description of one filesystem skill."""

    name: str
    description: str = ""
    when_to_use: str | None = None
    version: str | None = None
    tags: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    agent: dict[str, Any] = Field(default_factory=dict)
    paths: dict[str, Any] = Field(default_factory=dict)
    trusted: bool = False
    source: str = "filesystem"
    skill_dir: str
    path: str
    relative_path: str | None = None
    supporting_files: list[dict[str, Any]] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    digest: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tags", "allowed_tools")
    @classmethod
    def validate_string_lists(cls, value: list[str]) -> list[str]:
        """Normalize list fields to non-empty strings."""
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("context", "agent", "paths", "frontmatter")
    @classmethod
    def validate_json_dict(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Keep manifest dictionaries JSON-compatible."""
        return ensure_json_serializable(value, field_name="skill manifest")

    @field_validator("supporting_files")
    @classmethod
    def validate_supporting_files(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Keep supporting file index JSON-compatible."""
        return [
            ensure_json_serializable(item, field_name="skill supporting file")
            for item in value
        ]


class SkillInvocation(ContractModel):
    """Compact record persisted when a skill body or supporting file is viewed."""

    name: str
    path: str
    skill_dir: str
    digest: str
    trusted: bool
    agent_id: str | None = None
    content_kind: str
    relative_file: str | None = None
    tool_call_id: str | None = None


__all__ = ["SkillInvocation", "SkillManifest"]
