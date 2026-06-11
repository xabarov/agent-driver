"""Skill discovery and view built-in tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.skills import list_skill_manifests, skill_manifest_payload, view_skill
from agent_driver.tools.builtin.filesystem._paths import as_int, resolve_base_dir
from agent_driver.tools.context import get_workspace_cwd, get_workspace_jail_root
from agent_driver.tools.registry import ToolRegistry

_SKILL_TOOL = "skill_tool"
_SKILL_VIEW_TOOL = "skill_view"
_DEFAULT_MAX_RESULTS = 200
_DEFAULT_MAX_CHARS = 20000


def register_skill_tools(registry: ToolRegistry) -> None:
    """Register built-in skill discovery/view tools."""
    registry.register(_skill_tool_manifest(), _skill_tool_handler)
    registry.register(_skill_view_manifest(), _skill_view_handler)


def _skill_tool_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SKILL_TOOL,
        description=(
            "Discover SKILL.md files under a base directory and return path provenance "
            "plus trust classification."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "base_dir": {
                    "type": "string",
                    "description": (
                        "Directory to scan; absolute or relative to workspace cwd; "
                        "defaults to cwd"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum number of discovered skills",
                },
                "trusted_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Absolute directories treated as trusted skill sources"
                    ),
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden directories/files when scanning",
                },
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "skills": {"type": "array"},
                "returned_count": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
        output_type="json",
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_discovery",
            "application_tags": ["discovery"],
        },
    )


def _skill_view_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SKILL_VIEW_TOOL,
        description=(
            "Load a selected Agent Skill. Use this before relying on a skill; "
            "it returns full SKILL.md content or one supporting file plus "
            "trust and safety metadata."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=12000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "base_dir": {
                    "type": "string",
                    "description": "Base directory for name/relative path lookup",
                },
                "name": {"type": "string", "description": "Skill name to load"},
                "skill_dir": {
                    "type": "string",
                    "description": "Skill directory containing SKILL.md",
                },
                "path": {
                    "type": "string",
                    "description": "Path to SKILL.md or a skill directory",
                },
                "relative_file": {
                    "type": "string",
                    "description": "Optional supporting file path inside skill_dir",
                },
                "trusted_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Absolute directories treated as trusted skill roots"
                    ),
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 100000,
                    "description": "Maximum returned content characters",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional calling agent id for invocation record",
                },
            },
            "additionalProperties": False,
        },
        output_type="json",
        output_schema={
            "type": "object",
            "properties": {
                "skill": {"type": "object"},
                "content": {"type": "string"},
                "skill_invocation": {"type": "object"},
            },
        },
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_skill_view",
            "application_tags": ["discovery"],
        },
    )


async def _skill_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    max_results = as_int(args.get("max_results"), _DEFAULT_MAX_RESULTS, minimum=1)
    include_hidden = bool(args.get("include_hidden", False))
    trusted_roots = _normalize_trusted_roots(args.get("trusted_roots"))
    base = _resolve_skill_base_dir(args.get("base_dir"), trusted_roots=trusted_roots)
    manifests, truncated = list_skill_manifests(
        base_dir=base,
        trusted_roots=tuple(trusted_roots),
        include_hidden=include_hidden,
        max_results=max_results,
    )
    skills = []
    for manifest in manifests:
        payload = skill_manifest_payload(manifest)
        payload["provenance"] = {
            "base_dir": str(base),
            "source": manifest.source,
        }
        skills.append(payload)
    return {
        "summary": f"{len(skills)} skills discovered",
        "base_dir": str(base),
        "skills": skills,
        "returned_count": len(skills),
        "truncated": truncated,
        "max_results": max_results,
        "more_available": truncated,
    }


async def _skill_view_handler(args: dict[str, Any]) -> dict[str, Any]:
    trusted_roots = _normalize_trusted_roots(args.get("trusted_roots"))
    base = _resolve_skill_base_dir(args.get("base_dir"), trusted_roots=trusted_roots)
    max_chars = as_int(args.get("max_chars"), _DEFAULT_MAX_CHARS, minimum=1000)
    viewed = view_skill(
        base_dir=base,
        name=_optional_str(args.get("name")),
        skill_dir=_optional_str(args.get("skill_dir")),
        path=_optional_str(args.get("path")),
        relative_file=_optional_str(args.get("relative_file")),
        trusted_roots=tuple(trusted_roots),
        max_chars=max_chars,
        agent_id=_optional_str(args.get("agent_id")),
    )
    skill = skill_manifest_payload(viewed.manifest)
    return {
        "summary": (f"Loaded {viewed.content_kind} for skill '{viewed.manifest.name}'"),
        "skill": skill,
        "skill_dir": viewed.manifest.skill_dir,
        "supporting_files": viewed.manifest.supporting_files,
        "trusted": viewed.manifest.trusted,
        "safety_warnings": viewed.manifest.safety_warnings,
        "content_kind": viewed.content_kind,
        "content_path": viewed.content_path,
        "relative_file": viewed.relative_file,
        "content": viewed.content,
        "truncated": viewed.truncated,
        "skill_invocation": viewed.invocation.model_dump(mode="json"),
    }


def _resolve_skill_base_dir(raw: Any, *, trusted_roots: list[Path]) -> Path:
    """Resolve skill roots, allowing explicit trusted roots outside workspace jail."""
    if raw is None:
        return resolve_base_dir(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("base_dir must be a non-empty string when provided")
    base = Path(raw).expanduser()
    if not base.is_absolute():
        base = (get_workspace_cwd() / base).resolve()
    if not base.is_absolute():
        raise ValueError("base_dir must be absolute")
    if not base.exists():
        raise ValueError(f"base_dir does not exist: {base}")
    if not base.is_dir():
        raise ValueError(f"base_dir is not a directory: {base}")
    resolved = base.resolve()
    if _is_under_trusted_root(resolved, trusted_roots):
        return resolved
    jail_root = get_workspace_jail_root()
    if jail_root is not None:
        try:
            resolved.relative_to(jail_root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"path outside workspace ({jail_root.resolve()}): {resolved}"
            ) from exc
    return resolved


def _is_under_trusted_root(path: Path, trusted_roots: list[Path]) -> bool:
    for root in trusted_roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _normalize_trusted_roots(raw: Any) -> list[Path]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("trusted_roots must be an array of absolute directory paths")
    roots: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("trusted_roots entries must be non-empty strings")
        root = Path(item).expanduser()
        if not root.is_absolute():
            raise ValueError("trusted_roots entries must be absolute paths")
        if not root.exists() or not root.is_dir():
            raise ValueError(f"trusted_roots directory does not exist: {root}")
        roots.append(root.resolve())
    return roots


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


__all__ = ["register_skill_tools"]
