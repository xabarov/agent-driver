"""Subagent request built-in tool."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    SubagentExecutionMode,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.builtin._intent import build_intent_payload
from agent_driver.tools.registry import ToolRegistry

_AGENT_TOOL = "agent_tool"


def register_agent_tools(registry: ToolRegistry) -> None:
    """Register built-in subagent request tool."""
    registry.register(_agent_tool_manifest(), _agent_tool_handler)


def _agent_tool_manifest() -> ToolManifest:
    return ToolManifest(
        name=_AGENT_TOOL,
        description=(
            "Create a structured subagent spawn request payload for runtime "
            "orchestration."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Subagent task prompt"},
                "description": {
                    "type": "string",
                    "description": "Short user-facing task description",
                },
                "task_type": {
                    "type": "string",
                    "description": "Logical subagent task type",
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["sync", "background"],
                    "description": "Requested subagent execution mode",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Optional parent-scoped idempotency key",
                },
                "metadata": {"type": "object", "description": "Optional metadata"},
            },
            "required": ["task", "description"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={
            "implementation_status": "request_envelope",
            "adapter_kind": "subagent_orchestration",
            "application_tags": ["discovery", "collaboration", "intent"],
        },
    )


async def _agent_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    task = str(args.get("task") or "").strip()
    description = str(args.get("description") or "").strip()
    if not task:
        raise ValueError("task is required")
    if not description:
        raise ValueError("description is required")
    task_type = str(args.get("task_type") or "general").strip() or "general"
    execution_mode = str(args.get("execution_mode") or "sync").strip().lower()
    if execution_mode not in {
        SubagentExecutionMode.SYNC.value,
        SubagentExecutionMode.BACKGROUND.value,
    }:
        raise ValueError("execution_mode must be one of: sync, background")
    metadata = args.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    idempotency_key = str(args.get("idempotency_key") or "").strip() or None
    request = build_intent_payload(
        source_tool=_AGENT_TOOL,
        adapter_kind="subagent_orchestration",
        id_prefix="subreq",
        id_field="request_id",
        payload={
            "subagent_run_id": None,
            "task": task,
            "description": description,
            "task_type": task_type,
            "execution_mode": execution_mode,
            "idempotency_key": idempotency_key,
            "metadata": metadata,
        },
    )
    request["subagent_run_id"] = request["request_id"]
    return {
        "summary": f"subagent request prepared ({execution_mode})",
        "subagent_request": request,
    }


__all__ = ["register_agent_tools"]
