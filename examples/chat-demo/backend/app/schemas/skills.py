"""Schemas for chat-demo skill library endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillManifestView(BaseModel):
    """Metadata-first public view of one Agent Skill."""

    name: str
    description: str = ""
    when_to_use: str | None = Field(default=None, alias="whenToUse")
    tags: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    trusted: bool = False
    source: str = "filesystem"
    skill_dir: str = Field(alias="skillDir")
    path: str
    supporting_files: list[dict[str, object]] = Field(
        default_factory=list,
        alias="supportingFiles",
    )
    safety_warnings: list[str] = Field(default_factory=list, alias="safetyWarnings")
    digest: str

    model_config = {"populate_by_name": True}


class SkillsListResponse(BaseModel):
    """Skill library response."""

    skills: list[SkillManifestView]
    upload_enabled: bool = Field(default=True, alias="uploadEnabled")

    model_config = {"populate_by_name": True}


class SkillViewResponse(BaseModel):
    """Loaded skill body or supporting file response."""

    skill: SkillManifestView
    content: str
    content_kind: str = Field(alias="contentKind")
    content_path: str = Field(alias="contentPath")
    relative_file: str | None = Field(default=None, alias="relativeFile")
    truncated: bool = False
    skill_invocation: dict[str, object] = Field(alias="skillInvocation")

    model_config = {"populate_by_name": True}


class SkillUploadRequest(BaseModel):
    """Upload one SKILL.md body into the demo-local skill library."""

    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=100_000)


class SkillUploadResponse(BaseModel):
    """Uploaded skill metadata."""

    skill: SkillManifestView
