"""Product automation adapter tools (session-local intent records)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.registry import ToolRegistry

_WORKFLOW_TOOL = "workflow_tool"
_CRON_CREATE_TOOL = "cron_create_tool"
_CRON_DELETE_TOOL = "cron_delete_tool"
_CRON_LIST_TOOL = "cron_list_tool"
_REMOTE_TRIGGER_TOOL = "remote_trigger_tool"
_SUBSCRIBE_PR_TOOL = "subscribe_pr_tool"
_PUSH_NOTIFICATION_TOOL = "push_notification_tool"
_SEND_USER_FILE_TOOL = "send_user_file_tool"


@dataclass(slots=True)
class _AutomationStore:
    cron_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pr_subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_AUTOMATION_STORE = _AutomationStore()


def register_automation_tools(registry: ToolRegistry) -> None:
    """Register local intent automation adapter tools."""
    registry.register(_workflow_manifest(), _workflow_handler)
    registry.register(_cron_create_manifest(), _cron_create_handler)
    registry.register(_cron_delete_manifest(), _cron_delete_handler)
    registry.register(_cron_list_manifest(), _cron_list_handler)
    registry.register(_remote_trigger_manifest(), _remote_trigger_handler)
    registry.register(_subscribe_pr_manifest(), _subscribe_pr_handler)
    registry.register(_push_notification_manifest(), _push_notification_handler)
    registry.register(_send_user_file_manifest(), _send_user_file_handler)


def _workflow_manifest() -> ToolManifest:
    return ToolManifest(
        name=_WORKFLOW_TOOL,
        description="Queue session-local workflow execution intent.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}, "input": {"type": "object"}},
            "required": ["workflow_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _cron_create_manifest() -> ToolManifest:
    return ToolManifest(
        name=_CRON_CREATE_TOOL,
        description="Create session-local cron schedule intent.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {
                "job_name": {"type": "string"},
                "schedule": {"type": "string"},
                "command": {"type": "string"},
            },
            "required": ["job_name", "schedule", "command"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _cron_delete_manifest() -> ToolManifest:
    return ToolManifest(
        name=_CRON_DELETE_TOOL,
        description="Delete session-local cron schedule intent.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {"job_name": {"type": "string"}},
            "required": ["job_name"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _cron_list_manifest() -> ToolManifest:
    return ToolManifest(
        name=_CRON_LIST_TOOL,
        description="List session-local cron schedules.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        args_schema={"type": "object", "additionalProperties": False},
        output_type="json",
    )


def _remote_trigger_manifest() -> ToolManifest:
    return ToolManifest(
        name=_REMOTE_TRIGGER_TOOL,
        description="Queue remote trigger intent payload.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {"trigger_id": {"type": "string"}, "payload": {"type": "object"}},
            "required": ["trigger_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _subscribe_pr_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SUBSCRIBE_PR_TOOL,
        description="Create session-local PR subscription intent.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "pr_number": {"type": "integer"},
                "events": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo", "pr_number"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _push_notification_manifest() -> ToolManifest:
    return ToolManifest(
        name=_PUSH_NOTIFICATION_TOOL,
        description="Queue push notification intent payload.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _send_user_file_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SEND_USER_FILE_TOOL,
        description="Queue intent to send file artifact to user channel.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        args_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "channel": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _workflow_handler(args: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _required_str(args.get("workflow_id"), field="workflow_id")
    return {
        "summary": f"workflow queued: {workflow_id}",
        "workflow_event": {
            "event_id": f"wf_{uuid4().hex[:10]}",
            "workflow_id": workflow_id,
            "input": args.get("input") if isinstance(args.get("input"), dict) else {},
            "created_at": _utc_now(),
            "provenance": "session_local_automation",
        },
    }


async def _cron_create_handler(args: dict[str, Any]) -> dict[str, Any]:
    job_name = _required_str(args.get("job_name"), field="job_name")
    schedule = _required_str(args.get("schedule"), field="schedule")
    command = _required_str(args.get("command"), field="command")
    with _AUTOMATION_STORE.lock:
        if job_name in _AUTOMATION_STORE.cron_jobs:
            raise ValueError(f"cron job already exists: {job_name}")
        row = {
            "job_name": job_name,
            "schedule": schedule,
            "command": command,
            "created_at": _utc_now(),
        }
        _AUTOMATION_STORE.cron_jobs[job_name] = row
    return {"summary": f"cron created: {job_name}", "cron_job": row}


async def _cron_delete_handler(args: dict[str, Any]) -> dict[str, Any]:
    job_name = _required_str(args.get("job_name"), field="job_name")
    with _AUTOMATION_STORE.lock:
        row = _AUTOMATION_STORE.cron_jobs.pop(job_name, None)
    if row is None:
        raise ValueError(f"unknown cron job: {job_name}")
    return {"summary": f"cron deleted: {job_name}", "deleted_cron_job": row}


async def _cron_list_handler(_args: dict[str, Any]) -> dict[str, Any]:
    with _AUTOMATION_STORE.lock:
        rows = [_AUTOMATION_STORE.cron_jobs[name] for name in sorted(_AUTOMATION_STORE.cron_jobs)]
    return {"summary": f"{len(rows)} cron jobs listed", "cron_jobs": rows}


async def _remote_trigger_handler(args: dict[str, Any]) -> dict[str, Any]:
    trigger_id = _required_str(args.get("trigger_id"), field="trigger_id")
    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    return {
        "summary": f"remote trigger queued: {trigger_id}",
        "trigger_event": {
            "event_id": f"rt_{uuid4().hex[:10]}",
            "trigger_id": trigger_id,
            "payload": payload,
            "created_at": _utc_now(),
        },
    }


async def _subscribe_pr_handler(args: dict[str, Any]) -> dict[str, Any]:
    repo = _required_str(args.get("repo"), field="repo")
    pr_number = int(args.get("pr_number"))
    key = f"{repo}#{pr_number}"
    events = args.get("events") if isinstance(args.get("events"), list) else []
    with _AUTOMATION_STORE.lock:
        row = {
            "subscription_id": f"prsub_{uuid4().hex[:10]}",
            "repo": repo,
            "pr_number": pr_number,
            "events": [str(item) for item in events],
            "created_at": _utc_now(),
        }
        _AUTOMATION_STORE.pr_subscriptions[key] = row
    return {"summary": f"pr subscription created: {key}", "subscription": row}


async def _push_notification_handler(args: dict[str, Any]) -> dict[str, Any]:
    title = _required_str(args.get("title"), field="title")
    body = _required_str(args.get("body"), field="body")
    return {
        "summary": "push notification queued",
        "notification_event": {
            "event_id": f"pn_{uuid4().hex[:10]}",
            "title": title,
            "body": body,
            "created_at": _utc_now(),
        },
    }


async def _send_user_file_handler(args: dict[str, Any]) -> dict[str, Any]:
    file_path = _required_str(args.get("file_path"), field="file_path")
    return {
        "summary": f"user file send queued: {file_path}",
        "file_event": {
            "event_id": f"sf_{uuid4().hex[:10]}",
            "file_path": file_path,
            "channel": str(args.get("channel") or "default"),
            "caption": str(args.get("caption") or ""),
            "created_at": _utc_now(),
        },
    }


def _required_str(raw: Any, *, field: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _reset_automation_store_for_tests() -> None:
    with _AUTOMATION_STORE.lock:
        _AUTOMATION_STORE.cron_jobs.clear()
        _AUTOMATION_STORE.pr_subscriptions.clear()


__all__ = ["register_automation_tools", "_reset_automation_store_for_tests"]
