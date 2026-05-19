"""Worktree request-envelope built-in tools."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin._intent import build_intent_payload
from agent_driver.tools.registry import ToolRegistry

_ENTER_WORKTREE_TOOL = "enter_worktree_tool"
_EXIT_WORKTREE_TOOL = "exit_worktree_tool"


def register_worktree_tools(registry: ToolRegistry) -> None:
    """Register worktree request-envelope tools."""
    registry.register(_enter_worktree_manifest(), _enter_worktree_handler)
    registry.register(_exit_worktree_manifest(), _exit_worktree_handler)


def _enter_worktree_manifest() -> ToolManifest:
    return ToolManifest(
        name=_ENTER_WORKTREE_TOOL,
        description="Prepare high-risk worktree enter/create request envelope.",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.IRREVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ALWAYS,
        timeout_seconds=12.0,
        output_char_budget=4500,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "worktree_name": {"type": "string"},
                "base_ref": {"type": "string"},
                "target_path": {"type": "string"},
                "create_branch": {"type": "boolean"},
            },
            "required": ["worktree_name"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={
            "implementation_status": "request_envelope",
            "adapter_kind": "worktree",
            "application_tags": ["worktree", "intent"],
        },
    )


def _exit_worktree_manifest() -> ToolManifest:
    return ToolManifest(
        name=_EXIT_WORKTREE_TOOL,
        description="Prepare high-risk worktree exit/remove request envelope.",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.IRREVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ALWAYS,
        timeout_seconds=12.0,
        output_char_budget=4500,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "worktree_name": {"type": "string"},
                "target_path": {"type": "string"},
                "remove_branch": {"type": "boolean"},
            },
            "required": ["worktree_name"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={
            "implementation_status": "request_envelope",
            "adapter_kind": "worktree",
            "application_tags": ["worktree", "intent"],
        },
    )


async def _enter_worktree_handler(args: dict[str, Any]) -> dict[str, Any]:
    worktree_name = str(args.get("worktree_name") or "").strip()
    if not worktree_name:
        raise ValueError("worktree_name is required")
    payload = build_intent_payload(
        source_tool=_ENTER_WORKTREE_TOOL,
        adapter_kind="worktree",
        id_prefix="wreq",
        id_field="request_id",
        payload={
            "operation": "enter",
            "worktree_name": worktree_name,
            "base_ref": str(args.get("base_ref") or "HEAD").strip() or "HEAD",
            "target_path": str(args.get("target_path") or "").strip() or None,
            "create_branch": bool(args.get("create_branch", True)),
        },
    )
    return {
        "summary": f"worktree enter request prepared: {worktree_name}",
        "worktree_request": payload,
    }


async def _exit_worktree_handler(args: dict[str, Any]) -> dict[str, Any]:
    worktree_name = str(args.get("worktree_name") or "").strip()
    if not worktree_name:
        raise ValueError("worktree_name is required")
    payload = build_intent_payload(
        source_tool=_EXIT_WORKTREE_TOOL,
        adapter_kind="worktree",
        id_prefix="wreq",
        id_field="request_id",
        payload={
            "operation": "exit",
            "worktree_name": worktree_name,
            "target_path": str(args.get("target_path") or "").strip() or None,
            "remove_branch": bool(args.get("remove_branch", False)),
        },
    )
    return {
        "summary": f"worktree exit request prepared: {worktree_name}",
        "worktree_request": payload,
    }


__all__ = ["register_worktree_tools"]
