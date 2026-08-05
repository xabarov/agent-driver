"""Workspace artifact tools for durable research outputs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.contracts.execution import CapabilityName, ToolExecutionRequirement
from agent_driver.tools.builtin.filesystem._paths import as_int
from agent_driver.tools.context import (
    get_execution_lease,
    get_workspace_backend,
    get_workspace_cwd,
    get_workspace_jail_root,
)

ARTIFACT_LIST_TOOL = "artifact_list"
ARTIFACT_READ_TOOL = "artifact_read"
ARTIFACT_PREVIEW_TOOL = "artifact_preview"

_ARTIFACT_DIRS = ("research", "tool-results")
_DEFAULT_PREVIEW_BYTES = 16_000
_MAX_PREVIEW_BYTES = 128_000


def artifact_list_manifest() -> ToolManifest:
    """Build artifact_list manifest."""
    return ToolManifest(
        name=ARTIFACT_LIST_TOOL,
        description=(
            "List durable artifacts in the current workspace, such as "
            "research/report.md and tool-results files."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=8000,
        idempotent=True,
        execution_requirement=ToolExecutionRequirement(
            required=(CapabilityName.FILE_READ,)
        ),
        args_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["all", "research", "report", "tool_result"],
                    "description": "Optional artifact kind filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum artifacts to return.",
                },
            },
            "additionalProperties": False,
        },
        output_type="json",
        metadata={"application_tags": ["filesystem", "artifact", "research"]},
    )


def artifact_read_manifest() -> ToolManifest:
    """Build artifact_read manifest."""
    return ToolManifest(
        name=ARTIFACT_READ_TOOL,
        description=(
            "Read a bounded UTF-8 artifact by workspace-relative path. Use this "
            "instead of reprinting long research artifacts in chat."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=12000,
        idempotent=True,
        execution_requirement=ToolExecutionRequirement(
            required=(CapabilityName.FILE_READ,)
        ),
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Artifact path, e.g. research/report.md.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_PREVIEW_BYTES,
                    "description": "Maximum bytes to read.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={"application_tags": ["filesystem", "artifact", "research"]},
    )


def artifact_preview_manifest() -> ToolManifest:
    """Build artifact_preview manifest."""
    return ToolManifest(
        name=ARTIFACT_PREVIEW_TOOL,
        description=(
            "Return compact preview metadata and bounded text for a workspace "
            "artifact, optimized for checking report state before final answer."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=12000,
        idempotent=True,
        execution_requirement=ToolExecutionRequirement(
            required=(CapabilityName.FILE_READ,)
        ),
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Artifact path, e.g. research/report.md.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_PREVIEW_BYTES,
                    "description": "Maximum bytes to include in preview.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={"application_tags": ["filesystem", "artifact", "research"]},
    )


async def artifact_list_handler(args: dict[str, Any]) -> dict[str, Any]:
    """List known workspace artifacts."""
    kind_filter = str(args.get("kind") or "all").strip()
    if kind_filter not in {"all", "research", "report", "tool_result"}:
        raise ValueError("kind must be one of: all, research, report, tool_result")
    limit = as_int(args.get("limit"), default=100, minimum=1)
    all_artifacts = await _list_workspace_artifacts()
    artifacts = [
        item
        for item in all_artifacts
        if kind_filter == "all" or item["kind"] == kind_filter
    ][:limit]
    return {
        "summary": f"{len(artifacts)} artifact(s)",
        "artifacts": artifacts,
        "count": len(artifacts),
        "truncated": len(artifacts) == limit,
    }


async def artifact_read_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Read a bounded artifact."""
    artifact, content, truncated = await _read_artifact(
        args.get("path"),
        max_bytes=as_int(
            args.get("max_bytes"), default=_DEFAULT_PREVIEW_BYTES, minimum=1
        ),
    )
    return {
        "summary": f"{artifact['path']} ({artifact['size_bytes']} bytes)",
        **artifact,
        "content": content,
        "truncated": truncated,
    }


async def artifact_preview_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Return preview information for a bounded artifact."""
    artifact, content, truncated = await _read_artifact(
        args.get("path"),
        max_bytes=as_int(
            args.get("max_bytes"), default=_DEFAULT_PREVIEW_BYTES, minimum=1
        ),
    )
    headings = [
        line.strip() for line in content.splitlines() if line.lstrip().startswith("#")
    ][:20]
    return {
        "summary": f"{artifact['path']} preview ({len(content)} chars)",
        **artifact,
        "preview": content,
        "headings": headings,
        "truncated": truncated,
    }


async def _list_workspace_artifacts() -> list[dict[str, Any]]:
    backend = get_workspace_backend()
    if backend is not None:
        return await _routed_list_artifacts(backend)
    workspace = _workspace_root()
    rows: list[dict[str, Any]] = []
    for dirname in _ARTIFACT_DIRS:
        base = workspace / dirname
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            rows.append(_artifact_metadata(workspace, path))
    rows.sort(key=lambda item: item["path"])
    return rows


async def _read_artifact(
    raw_path: Any, *, max_bytes: int
) -> tuple[dict[str, Any], str, bool]:
    backend = get_workspace_backend()
    if backend is not None:
        return await _routed_read_artifact(backend, raw_path, max_bytes=max_bytes)
    workspace = _workspace_root()
    path = _resolve_artifact_path(workspace, raw_path)
    size = path.stat().st_size
    limit = min(max_bytes, _MAX_PREVIEW_BYTES)
    truncated = size > limit
    content = path.read_bytes()[:limit].decode("utf-8", errors="replace")
    return _artifact_metadata(workspace, path), content, truncated


def _routed_workspace_root() -> str:
    lease = get_execution_lease()
    paths = getattr(lease, "paths", None)
    if paths is not None:
        return str(paths.workspace_root).rstrip("/")
    jail = get_workspace_jail_root()
    return str(jail).rstrip("/") if jail is not None else str(get_workspace_cwd())


async def _routed_list_artifacts(backend: Any) -> list[dict[str, Any]]:
    from agent_driver.contracts.execution_workspace import ExecutionListRequest
    from agent_driver.execution.adapters import identity_from_context

    root = _routed_workspace_root()
    rows: list[dict[str, Any]] = []
    for dirname in _ARTIFACT_DIRS:
        res = await backend.list_dir(
            ExecutionListRequest(
                identity=identity_from_context(
                    getattr(backend, "backend_id", "backend")
                ),
                path=f"{root}/{dirname}",
                recursive=True,
                max_entries=1000,
            )
        )
        for entry in res.entries:
            if entry.is_dir:
                continue
            relative = entry.path[len(root) + 1 :]
            rows.append(
                {
                    "path": relative,
                    "kind": _artifact_kind(relative),
                    "size_bytes": entry.size_bytes or 0,
                    "modified_at": None,
                }
            )
    rows.sort(key=lambda item: item["path"])
    return rows


async def _routed_read_artifact(
    backend: Any, raw_path: Any, *, max_bytes: int
) -> tuple[dict[str, Any], str, bool]:
    from agent_driver.contracts.execution import ExecutionReadRequest
    from agent_driver.execution.adapters import identity_from_context
    from agent_driver.execution.pathsafety import (
        WorkspacePathError,
        validate_workspace_path,
    )

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("path must be a non-empty string")
    relative = raw_path.strip().lstrip("/")
    if not _is_artifact_relative_path(relative):
        raise ValueError("path is not a known artifact path")
    root = _routed_workspace_root()
    lease = get_execution_lease()
    paths = getattr(lease, "paths", None)
    full = f"{root}/{relative}"
    if paths is not None:
        # traversal safety against the backend workspace contract
        try:
            full = validate_workspace_path(full, paths)
        except WorkspacePathError:
            raise
    limit = min(max_bytes, _MAX_PREVIEW_BYTES)
    read = await backend.read_text(
        ExecutionReadRequest(
            identity=identity_from_context(getattr(backend, "backend_id", "backend")),
            path=full,
            max_bytes=_MAX_PREVIEW_BYTES,
        )
    )
    size = read.size_bytes
    truncated = size > limit
    content = read.content[:limit]
    meta = {
        "path": relative,
        "kind": _artifact_kind(relative),
        "size_bytes": size,
        "modified_at": None,
    }
    return meta, content, truncated


def _workspace_root() -> Path:
    root = get_workspace_jail_root() or get_workspace_cwd()
    return root.resolve()


def _resolve_artifact_path(workspace: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("path must be a non-empty string")
    relative = raw_path.strip().lstrip("/")
    if not _is_artifact_relative_path(relative):
        raise ValueError("path is not a known artifact path")
    path = (workspace / relative).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"path outside workspace: {path}") from exc
    if not path.exists():
        raise ValueError(f"artifact does not exist: {relative}")
    if not path.is_file():
        raise ValueError(f"artifact path is not a file: {relative}")
    return path


def _artifact_metadata(workspace: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(workspace).as_posix()
    stat = path.stat()
    return {
        "path": relative,
        "kind": _artifact_kind(relative),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


def _is_artifact_relative_path(path: str) -> bool:
    return any(path.startswith(f"{dirname}/") for dirname in _ARTIFACT_DIRS)


def _artifact_kind(path: str) -> str:
    if path == "research/report.md":
        return "report"
    if path.startswith("research/"):
        return "research"
    if path.startswith("tool-results/"):
        return "tool_result"
    return "file"
