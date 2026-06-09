"""Filesystem skill registry helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_driver.security.context_scan import scan_context_text
from agent_driver.skills.models import SkillInvocation, SkillManifest
from agent_driver.skills.parser import SKILL_FILENAME, load_skill_manifest


@dataclass(frozen=True, slots=True)
class SkillView:
    """Loaded skill content or supporting file content."""

    manifest: SkillManifest
    content: str
    content_kind: str
    content_path: str
    relative_file: str | None
    truncated: bool
    invocation: SkillInvocation


def list_skill_manifests(
    *,
    base_dir: Path,
    trusted_roots: tuple[Path, ...] = (),
    include_hidden: bool = False,
    max_results: int = 200,
) -> tuple[list[SkillManifest], bool]:
    """Discover SKILL.md files and return metadata manifests."""
    base = base_dir.expanduser().resolve()
    manifests: list[SkillManifest] = []
    truncated = False
    for path in sorted(base.rglob(SKILL_FILENAME)):
        if len(manifests) >= max_results:
            truncated = True
            break
        if not include_hidden and _is_hidden_path(path=path, base=base):
            continue
        manifests.append(
            load_skill_manifest(
                path,
                base_dir=base,
                trusted_roots=trusted_roots,
            )
        )
    return manifests, truncated


def view_skill(
    *,
    base_dir: Path,
    name: str | None = None,
    skill_dir: str | None = None,
    path: str | None = None,
    relative_file: str | None = None,
    trusted_roots: tuple[Path, ...] = (),
    max_chars: int = 20000,
    agent_id: str | None = None,
    tool_call_id: str | None = None,
) -> SkillView:
    """Load a skill body or one supporting file with compact invocation data."""
    skill_path = _resolve_skill_path(
        base_dir=base_dir,
        name=name,
        skill_dir=skill_dir,
        path=path,
        trusted_roots=trusted_roots,
    )
    manifest = load_skill_manifest(
        skill_path,
        base_dir=base_dir.expanduser().resolve(),
        trusted_roots=trusted_roots,
    )
    target_path = skill_path
    content_kind = "skill"
    clean_relative_file = None
    if relative_file:
        clean_relative_file = _clean_relative_file(relative_file)
        target_path = (skill_path.parent / clean_relative_file).resolve()
        target_path.relative_to(skill_path.parent.resolve())
        if not target_path.is_file():
            raise FileNotFoundError(f"supporting file not found: {clean_relative_file}")
        content_kind = "supporting_file"
    content = target_path.read_text(encoding="utf-8")
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    # E3: untrusted skill files are ingested into the prompt — scan them and
    # substitute a blocking placeholder on an injection/C2 hit. Trusted skills
    # are author-controlled and pass through unchanged.
    if not manifest.trusted:
        scan = scan_context_text(content, source=f"skill:{manifest.name}")
        if scan.flagged:
            content = scan.safe_text
    invocation = SkillInvocation(
        name=manifest.name,
        path=manifest.path,
        skill_dir=manifest.skill_dir,
        digest=manifest.digest,
        trusted=manifest.trusted,
        agent_id=agent_id,
        content_kind=content_kind,
        relative_file=clean_relative_file,
        tool_call_id=tool_call_id,
    )
    return SkillView(
        manifest=manifest,
        content=content,
        content_kind=content_kind,
        content_path=str(target_path),
        relative_file=clean_relative_file,
        truncated=truncated,
        invocation=invocation,
    )


def _resolve_skill_path(
    *,
    base_dir: Path,
    name: str | None,
    skill_dir: str | None,
    path: str | None,
    trusted_roots: tuple[Path, ...],
) -> Path:
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        resolved = candidate.resolve()
        if resolved.is_dir():
            resolved = resolved / SKILL_FILENAME
        if not resolved.is_file():
            raise FileNotFoundError(f"skill file not found: {resolved}")
        return resolved
    if skill_dir:
        candidate = Path(skill_dir).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        resolved = (
            (candidate.resolve() / SKILL_FILENAME)
            if candidate.is_dir()
            else candidate.resolve()
        )
        if not resolved.is_file():
            raise FileNotFoundError(f"skill file not found: {resolved}")
        return resolved
    if not name:
        raise ValueError("skill_view requires one of name, skill_dir or path")
    manifests, _truncated = list_skill_manifests(
        base_dir=base_dir,
        trusted_roots=trusted_roots,
        max_results=1000,
    )
    matches = [manifest for manifest in manifests if manifest.name == name]
    if not matches:
        raise FileNotFoundError(f"skill not found by name: {name}")
    if len(matches) > 1:
        raise ValueError(f"skill name is ambiguous: {name}")
    return Path(matches[0].path)


def _clean_relative_file(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or ".." in Path(cleaned).parts:
        raise ValueError("relative_file must stay inside the skill directory")
    return cleaned


def _is_hidden_path(*, path: Path, base: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(base).parts)


def skill_manifest_payload(manifest: SkillManifest) -> dict[str, Any]:
    """Return JSON payload for a skill manifest."""
    return manifest.model_dump(mode="json")


__all__ = [
    "SkillView",
    "list_skill_manifests",
    "skill_manifest_payload",
    "view_skill",
]
