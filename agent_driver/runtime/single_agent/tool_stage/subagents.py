"""Subagent-related tool-stage post-processing."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerDeps,
)
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.subagents import append_subagent_continuation, stop_subagent_run


class ToolStageSubagentHost(Protocol):
    """Host surface required for subagent tool-stage helpers."""

    _deps: RunnerDeps

    def _emit(self, event: EventSpec) -> None: ...


def apply_agent_tool_spawn_requests(
    context: RunContext, result: ToolExecutionResult
) -> None:
    """Turn successful ``agent_tool`` envelopes into runtime subagent plans."""
    tasks: list[dict[str, object]] = []
    for envelope in result.envelopes:
        if envelope.call.tool_name != "agent_tool":
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        request = structured.get("subagent_request")
        if not isinstance(request, dict):
            continue
        task = str(request.get("task") or "").strip()
        description = str(request.get("description") or task or "subagent task").strip()
        if not task:
            continue
        request_id = str(
            request.get("request_id") or envelope.call.tool_call_id
        ).strip()
        task_id = request_id or f"task_{len(tasks) + 1}"
        idempotency_key = request.get("idempotency_key")
        tasks.append(
            {
                "task_id": task_id,
                "task": task,
                "description": description,
                "idempotency_key": (
                    str(idempotency_key) if idempotency_key is not None else task_id
                ),
            }
        )
    if not tasks:
        return
    existing = context.metadata.get("planned_subagent_group")
    if isinstance(existing, dict) and isinstance(existing.get("tasks"), list):
        merged_tasks = [item for item in existing["tasks"] if isinstance(item, dict)]
        merged_tasks.extend(tasks)
        context.metadata["planned_subagent_group"] = {**existing, "tasks": merged_tasks}
        return
    context.metadata["planned_subagent_group"] = {
        "group_id": f"group_{context.run_id}_agent_tool",
        "purpose": "agent_tool_spawn",
        "join_policy": "wait_all",
        "merge_mode": "append",
        "tasks": tasks,
        "source": "agent_tool",
    }


def apply_subagent_control_tool_outputs(
    host: ToolStageSubagentHost, context: RunContext, result: ToolExecutionResult
) -> None:
    """Apply parent-to-child continuation/stop tool outputs to subagent rows."""
    for envelope in result.envelopes:
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        if envelope.call.tool_name == "send_message_tool":
            _apply_subagent_continuation_output(host, context, structured)
        elif envelope.call.tool_name == "task_stop_tool":
            _apply_subagent_stop_output(host, context, structured)


def _apply_subagent_continuation_output(
    host: ToolStageSubagentHost, context: RunContext, structured: dict[str, Any]
) -> None:
    message_event = structured.get("message_event")
    if not isinstance(message_event, dict):
        return
    recipient = _clean_optional_text(message_event.get("recipient"))
    message = _clean_optional_text(message_event.get("message"))
    if recipient is None or message is None:
        return
    metadata = message_event.get("metadata")
    updated = append_subagent_continuation(
        host._deps.subagent_store,
        parent_run_id=context.run_id,
        subagent_run_id=recipient,
        child_run_id=recipient,
        message=message,
        metadata=metadata if isinstance(metadata, dict) else None,
        mailbox_store=host._deps.subagent_mailbox_store,
    )
    if updated is None:
        return
    _refresh_subagent_metadata(host, context)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.CONTROL_APPLIED,
        payload={
            "kind": "subagent_continuation",
            "subagent_run_id": updated.subagent_run_id,
            "child_run_id": updated.child_run_id,
            "messages": len(updated.metadata.get("continuation_messages") or []),
        },
    )


def _apply_subagent_stop_output(
    host: ToolStageSubagentHost, context: RunContext, structured: dict[str, Any]
) -> None:
    stop_payload = structured.get("subagent_stop")
    if not isinstance(stop_payload, dict):
        return
    subagent_run_id = _clean_optional_text(
        stop_payload.get("subagent_run_id") or stop_payload.get("task_id")
    )
    child_run_id = _clean_optional_text(stop_payload.get("child_run_id"))
    updated = stop_subagent_run(
        host._deps.subagent_store,
        parent_run_id=context.run_id,
        subagent_run_id=subagent_run_id,
        child_run_id=child_run_id,
        reason=_clean_optional_text(stop_payload.get("reason")),
    )
    if updated is None:
        return
    _refresh_subagent_metadata(host, context)
    payload = {
        "subagent_run_id": updated.subagent_run_id,
        "child_run_id": updated.child_run_id,
        "status": updated.status.value,
        "terminal_state": (
            updated.terminal_state.value if updated.terminal_state is not None else None
        ),
        "reason": updated.metadata.get("stop_reason"),
    }
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.SUBAGENT_COMPLETED,
        payload=payload,
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.CONTROL_APPLIED,
        payload={"kind": "subagent_stop", **payload},
    )


def _refresh_subagent_metadata(
    host: ToolStageSubagentHost, context: RunContext
) -> None:
    context.metadata["subagent_runs"] = [
        row.model_dump(mode="json")
        for row in host._deps.subagent_store.list_runs(context.run_id)
    ]


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


__all__ = [
    "apply_agent_tool_spawn_requests",
    "apply_subagent_control_tool_outputs",
]
