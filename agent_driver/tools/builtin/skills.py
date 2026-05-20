"""Skill discovery built-in tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import as_int, resolve_base_dir
from agent_driver.tools.registry import ToolRegistry

_SKILL_TOOL = "skill_tool"
_DEFAULT_MAX_RESULTS = 200


def register_skill_tools(registry: ToolRegistry) -> None:
    """Register built-in skill discovery tool."""
    registry.register(_skill_tool_manifest(), _skill_tool_handler)


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
        output_type="json",
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_discovery",
            "application_tags": ["discovery"],
        },
    )


async def _skill_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    base = resolve_base_dir(args.get("base_dir"))
    max_results = as_int(args.get("max_results"), _DEFAULT_MAX_RESULTS, minimum=1)
    include_hidden = bool(args.get("include_hidden", False))
    trusted_roots = _normalize_trusted_roots(args.get("trusted_roots"))
    skills: list[dict[str, Any]] = []
    truncated = False
    for path in sorted(base.rglob("SKILL.md")):
        if len(skills) >= max_results:
            truncated = True
            break
        rel = path.relative_to(base).as_posix()
        if not include_hidden and _is_hidden_path(path=path, base=base):
            continue
        parent = path.parent
        skills.append(
            {
                "name": parent.name,
                "path": str(path),
                "relative_path": rel,
                "trusted": _is_trusted(path, trusted_roots),
                "provenance": {
                    "base_dir": str(base),
                    "source": "filesystem",
                },
            }
        )
    return {
        "summary": f"{len(skills)} skills discovered",
        "base_dir": str(base),
        "skills": skills,
        "returned_count": len(skills),
        "truncated": truncated,
        "max_results": max_results,
        "more_available": truncated,
    }


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


def _is_hidden_path(*, path: Path, base: Path) -> bool:
    relative_parts = path.relative_to(base).parts
    return any(part.startswith(".") for part in relative_parts)


def _is_trusted(path: Path, trusted_roots: list[Path]) -> bool:
    if not trusted_roots:
        return False
    resolved = path.resolve()
    for root in trusted_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


__all__ = ["register_skill_tools"]
