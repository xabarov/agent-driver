"""Skill library endpoints backed by shared agent_driver skill contracts."""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas.skills import (
    SkillManifestView,
    SkillsListResponse,
    SkillUploadRequest,
    SkillUploadResponse,
    SkillViewResponse,
)
from fastapi import APIRouter, HTTPException

from agent_driver.skills import (
    curated_skills_dir,
    list_skill_manifests,
    skill_manifest_payload,
    view_skill,
)

router = APIRouter(tags=["skills"])

_UPLOAD_ROOT = Path(".agent-driver") / "skills"
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _trusted_roots() -> tuple[Path, ...]:
    return (curated_skills_dir(), _UPLOAD_ROOT.resolve())


def _skill_view(manifest_payload: dict[str, object]) -> SkillManifestView:
    return SkillManifestView.model_validate(manifest_payload)


def _skill_rows() -> list[SkillManifestView]:
    rows: list[SkillManifestView] = []
    for base in (curated_skills_dir(), _UPLOAD_ROOT):
        if not base.exists():
            continue
        manifests, _truncated = list_skill_manifests(
            base_dir=base,
            trusted_roots=_trusted_roots(),
            include_hidden=False,
            max_results=500,
        )
        rows.extend(_skill_view(skill_manifest_payload(item)) for item in manifests)
    return sorted(rows, key=lambda item: (not item.trusted, item.name))


@router.get("/skills", response_model=SkillsListResponse)
def list_skills() -> SkillsListResponse:
    """List curated and demo-uploaded skills using shared skill registry."""
    return SkillsListResponse(skills=_skill_rows(), uploadEnabled=True)


@router.get("/skills/{name}", response_model=SkillViewResponse)
def get_skill(name: str, relative_file: str | None = None) -> SkillViewResponse:
    """Load a selected skill body or supporting file through skill_view helper."""
    try:
        loaded = view_skill(
            base_dir=curated_skills_dir(),
            name=name,
            relative_file=relative_file,
            trusted_roots=_trusted_roots(),
            max_chars=30_000,
            agent_id="chat-demo",
        )
    except FileNotFoundError:
        try:
            loaded = view_skill(
                base_dir=_UPLOAD_ROOT,
                name=name,
                relative_file=relative_file,
                trusted_roots=_trusted_roots(),
                max_chars=30_000,
                agent_id="chat-demo",
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillViewResponse(
        skill=_skill_view(skill_manifest_payload(loaded.manifest)),
        content=loaded.content,
        contentKind=loaded.content_kind,
        contentPath=loaded.content_path,
        relativeFile=loaded.relative_file,
        truncated=loaded.truncated,
        skillInvocation=loaded.invocation.model_dump(mode="json"),
    )


@router.post("/skills/uploads", response_model=SkillUploadResponse)
def upload_skill(body: SkillUploadRequest) -> SkillUploadResponse:
    """Install a demo-local skill; parsing remains in agent_driver.skills."""
    slug = _SAFE_NAME_RE.sub("-", body.name.strip()).strip(".-").lower()
    if not slug:
        raise HTTPException(status_code=400, detail="invalid skill name")
    skill_dir = _UPLOAD_ROOT / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(body.content, encoding="utf-8")
    manifests, _truncated = list_skill_manifests(
        base_dir=_UPLOAD_ROOT,
        trusted_roots=_trusted_roots(),
        include_hidden=False,
        max_results=500,
    )
    for manifest in manifests:
        if Path(manifest.path) == skill_path.resolve():
            return SkillUploadResponse(
                skill=_skill_view(skill_manifest_payload(manifest))
            )
    raise HTTPException(status_code=500, detail="uploaded skill was not indexed")
