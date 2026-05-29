"""Session-local task and monitor built-in tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolRegistry

_TASK_CREATE_TOOL = "task_create"
_TASK_GET_TOOL = "task_get"
_TASK_LIST_TOOL = "task_list"
_TASK_UPDATE_TOOL = "task_update"
_TASK_OUTPUT_TOOL = "task_output"
_TASK_STOP_TOOL = "task_stop_tool"
_MONITOR_TOOL = "monitor_tool"
_SLEEP_TOOL = "sleep_tool"
_TASK_STATUS_VALUES = {"running", "completed", "failed", "timed_out", "killed"}
_OUTPUT_PREVIEW_CHARS_DEFAULT = 2_000


@dataclass(slots=True)
class _TaskEntry:
    task_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    output_chunks: list[dict[str, str]] = field(default_factory=list)

    def as_dict(
        self, *, include_output: bool = False, preview_chars: int = 2_000
    ) -> dict[str, Any]:
        """Return JSON-ready task payload with optional bounded output."""
        payload = {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }
        if include_output:
            payload["output"] = [
                {
                    "source": row["source"],
                    "text_preview": row["text"][:preview_chars],
                }
                for row in self.output_chunks
            ]
        return payload


@dataclass(slots=True)
class _TaskStore:
    by_id: dict[str, _TaskEntry] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def create(self, *, title: str, metadata: dict[str, Any]) -> _TaskEntry:
        """Create task with unique title guard among running tasks."""
        now = _utc_now()
        with self.lock:
            for task_id in self.order:
                current = self.by_id[task_id]
                if current.status == "running" and current.title == title:
                    raise ValueError("running task with same title already exists")
            task = _TaskEntry(
                task_id=f"task_{uuid4().hex[:10]}",
                title=title,
                status="running",
                created_at=now,
                updated_at=now,
                metadata=metadata,
            )
            self.by_id[task.task_id] = task
            self.order.append(task.task_id)
            return task

    def get(self, task_id: str) -> _TaskEntry:
        """Load one task by identifier."""
        with self.lock:
            task = self.by_id.get(task_id)
            if task is None:
                raise ValueError(f"unknown task_id: {task_id}")
            return task

    def list(self, *, status: str | None = None) -> list[_TaskEntry]:
        """List tasks optionally filtered by status."""
        with self.lock:
            rows = [self.by_id[task_id] for task_id in self.order]
            if status is None:
                return rows
            return [row for row in rows if row.status == status]

    def update(
        self,
        *,
        task_id: str,
        status: str | None,
        metadata_patch: dict[str, Any] | None,
        output_chunk: dict[str, str] | None,
    ) -> _TaskEntry:
        """Update task state and optionally append one output chunk."""
        with self.lock:
            task = self.by_id.get(task_id)
            if task is None:
                raise ValueError(f"unknown task_id: {task_id}")
            if status is not None:
                task.status = status
            if metadata_patch:
                task.metadata = {**task.metadata, **metadata_patch}
            if output_chunk:
                task.output_chunks.append(output_chunk)
            task.updated_at = _utc_now()
            return task


_TASK_STORE = _TaskStore()


def register_tasking_tools(registry: ToolRegistry) -> None:
    """Register session-local tasking tools."""
    registry.register(_task_create_manifest(), _task_create_handler)
    registry.register(_task_get_manifest(), _task_get_handler)
    registry.register(_task_list_manifest(), _task_list_handler)
    registry.register(_task_update_manifest(), _task_update_handler)
    registry.register(_task_output_manifest(), _task_output_handler)
    registry.register(_task_stop_manifest(), _task_stop_handler)
    registry.register(_monitor_manifest(), _monitor_handler)
    registry.register(_sleep_manifest(), _sleep_handler)


def _task_create_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_CREATE_TOOL,
        description="Create a durable task row for long-running work tracking.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "metadata": {"type": "object", "description": "Task metadata patch"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _task_get_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_GET_TOOL,
        description="Read one task row by task_id with bounded output previews.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "include_output": {
                    "type": "boolean",
                    "description": "Include bounded output previews",
                },
                "max_preview_chars": {
                    "type": "integer",
                    "minimum": 32,
                    "maximum": 50_000,
                    "description": "Maximum output chars per chunk",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _task_list_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_LIST_TOOL,
        description="List task rows optionally filtered by status.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": sorted(_TASK_STATUS_VALUES),
                    "description": "Optional status filter",
                }
            },
            "additionalProperties": False,
        },
        output_type="json",
    )


def _task_update_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_UPDATE_TOOL,
        description="Update task status and metadata patch.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "status": {
                    "type": "string",
                    "enum": sorted(_TASK_STATUS_VALUES),
                    "description": "New task status",
                },
                "metadata_patch": {
                    "type": "object",
                    "description": "Partial metadata update",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _task_output_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_OUTPUT_TOOL,
        description="Append bounded output chunk to a task row.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "source": {
                    "type": "string",
                    "enum": ["stdout", "stderr", "log"],
                    "description": "Output stream source",
                },
                "text": {"type": "string", "description": "Output text chunk"},
                "max_chars": {
                    "type": "integer",
                    "minimum": 32,
                    "maximum": 50_000,
                    "description": "Maximum saved chars for text chunk",
                },
            },
            "required": ["task_id", "text"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _task_stop_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TASK_STOP_TOOL,
        description="Stop a running task by setting terminal status.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task identifier or subagent_run_id fallback",
                },
                "subagent_run_id": {
                    "type": "string",
                    "description": "Optional native subagent run identifier to stop",
                },
                "child_run_id": {
                    "type": "string",
                    "description": "Optional child runtime run_id to stop",
                },
                "status": {
                    "type": "string",
                    "enum": ["killed", "timed_out", "failed", "completed"],
                    "description": "Terminal status to set (default: killed)",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional stop reason for native subagent runs",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _monitor_manifest() -> ToolManifest:
    return ToolManifest(
        name=_MONITOR_TOOL,
        description="Read bounded monitoring view for one task.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "max_preview_chars": {
                    "type": "integer",
                    "minimum": 32,
                    "maximum": 50_000,
                    "description": "Maximum chars per output preview row",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _sleep_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SLEEP_TOOL,
        description="Wait for bounded seconds and return wake metadata.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.NONE,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=15.0,
        output_char_budget=2000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 5.0,
                    "description": "Sleep duration in seconds (max 5)",
                }
            },
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _task_create_handler(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    metadata = args.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    task = _TASK_STORE.create(title=title, metadata=metadata)
    return {
        "summary": f"task created: {task.task_id}",
        "task": task.as_dict(include_output=False),
    }


async def _task_get_handler(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    include_output = bool(args.get("include_output", False))
    preview_chars = _as_int(
        args.get("max_preview_chars"),
        default=_OUTPUT_PREVIEW_CHARS_DEFAULT,
        minimum=32,
    )
    task = _TASK_STORE.get(task_id)
    return {
        "summary": f"task loaded: {task.task_id}",
        "task": task.as_dict(
            include_output=include_output, preview_chars=preview_chars
        ),
    }


async def _task_list_handler(args: dict[str, Any]) -> dict[str, Any]:
    status_raw = args.get("status")
    status = None
    if status_raw is not None:
        status = str(status_raw).strip()
        if status not in _TASK_STATUS_VALUES:
            raise ValueError(f"status must be one of: {sorted(_TASK_STATUS_VALUES)}")
    rows = _TASK_STORE.list(status=status)
    return {
        "summary": f"{len(rows)} tasks listed",
        "tasks": [row.as_dict(include_output=False) for row in rows],
    }


async def _task_update_handler(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    status_raw = args.get("status")
    status = None
    if status_raw is not None:
        status = str(status_raw).strip()
        if status not in _TASK_STATUS_VALUES:
            raise ValueError(f"status must be one of: {sorted(_TASK_STATUS_VALUES)}")
    metadata_patch = args.get("metadata_patch")
    if metadata_patch is not None and not isinstance(metadata_patch, dict):
        raise ValueError("metadata_patch must be an object")
    task = _TASK_STORE.update(
        task_id=task_id,
        status=status,
        metadata_patch=metadata_patch,
        output_chunk=None,
    )
    return {
        "summary": f"task updated: {task.task_id}",
        "task": task.as_dict(include_output=False),
    }


async def _task_output_handler(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    source = str(args.get("source") or "log").strip().lower()
    if source not in {"stdout", "stderr", "log"}:
        raise ValueError("source must be one of: stdout, stderr, log")
    max_chars = _as_int(args.get("max_chars"), default=2000, minimum=32)
    task = _TASK_STORE.update(
        task_id=task_id,
        status=None,
        metadata_patch=None,
        output_chunk={"source": source, "text": text[:max_chars]},
    )
    return {
        "summary": f"task output appended: {task.task_id}",
        "task": task.as_dict(include_output=True, preview_chars=max_chars),
    }


async def _task_stop_handler(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    status = str(args.get("status") or "killed").strip()
    if status not in {"killed", "timed_out", "failed", "completed"}:
        raise ValueError("status must be one of: killed, timed_out, failed, completed")
    subagent_run_id = str(args.get("subagent_run_id") or "").strip()
    child_run_id = str(args.get("child_run_id") or "").strip()
    if subagent_run_id or child_run_id:
        stop_payload = {
            "task_id": task_id,
            "subagent_run_id": subagent_run_id or task_id,
            "child_run_id": child_run_id or None,
            "status": status,
            "reason": str(args.get("reason") or "parent_requested_stop").strip(),
        }
        return {
            "summary": f"subagent stop requested: {subagent_run_id or child_run_id}",
            "subagent_stop": stop_payload,
        }
    task = _TASK_STORE.update(
        task_id=task_id,
        status=status,
        metadata_patch=None,
        output_chunk=None,
    )
    return {
        "summary": f"task stopped: {task.task_id} ({status})",
        "task": task.as_dict(include_output=False),
    }


async def _monitor_handler(args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    preview_chars = _as_int(
        args.get("max_preview_chars"),
        default=_OUTPUT_PREVIEW_CHARS_DEFAULT,
        minimum=32,
    )
    task = _TASK_STORE.get(task_id)
    payload = task.as_dict(include_output=True, preview_chars=preview_chars)
    output_rows = payload.get("output", [])
    return {
        "summary": f"task monitor view: {task.task_id}",
        "task_id": task.task_id,
        "status": task.status,
        "updated_at": task.updated_at,
        "output_rows": output_rows,
    }


async def _sleep_handler(args: dict[str, Any]) -> dict[str, Any]:
    seconds = _as_float(args.get("seconds"), default=0.1, minimum=0.0, maximum=5.0)
    await asyncio.sleep(seconds)
    return {
        "summary": f"slept for {seconds:.3f}s",
        "slept_seconds": seconds,
        "woke_at": _utc_now(),
    }


def _as_int(raw: Any, *, default: int, minimum: int) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def _as_float(raw: Any, *, default: float, minimum: float, maximum: float) -> float:
    if raw is None:
        return default
    value = float(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    if value > maximum:
        raise ValueError(f"value must be <= {maximum}")
    return value


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _reset_task_store_for_tests() -> None:
    """Reset shared task store for deterministic tests."""
    with _TASK_STORE.lock:
        _TASK_STORE.by_id.clear()
        _TASK_STORE.order.clear()


__all__ = ["register_tasking_tools", "_reset_task_store_for_tests"]
