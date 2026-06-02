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
    max_subagent_requests = _max_subagent_requests(context)
    planned_count = _planned_or_started_subagent_count(context)
    tasks: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for envelope in result.envelopes:
        if envelope.call.tool_name != "agent_tool":
            continue
        if len(tasks) + planned_count >= max_subagent_requests:
            skipped.append(
                {
                    "tool_call_id": envelope.call.tool_call_id,
                    "reason": "max_subagent_requests",
                    "limit": max_subagent_requests,
                }
            )
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
        metadata = request.get("metadata")
        task_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        task_text = task
        if _deep_research_mode(context):
            task_metadata.setdefault("worker_type", "researcher")
            task_metadata["deep_research_child_notes_only"] = True
            task_text = _deep_research_child_task(task_text)
        tasks.append(
            {
                "task_id": task_id,
                "task": task_text,
                "description": description,
                "idempotency_key": (
                    str(idempotency_key) if idempotency_key is not None else task_id
                ),
                "metadata": task_metadata,
            }
        )
    if skipped:
        _record_subagent_backpressure(context, skipped)
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


def _deep_research_mode(context: RunContext) -> bool:
    metadata = context.run_input.tool_policy.metadata
    deep_mode = metadata.get("deep_research_mode")
    if isinstance(deep_mode, dict) and deep_mode.get("enabled") is True:
        return True
    task_contract = metadata.get("task_contract")
    return (
        isinstance(task_contract, dict)
        and task_contract.get("research_mode") == "deep"
    )


def _deep_research_profile(context: RunContext) -> str:
    metadata = context.run_input.tool_policy.metadata
    deep_mode = metadata.get("deep_research_mode")
    if isinstance(deep_mode, dict):
        profile = deep_mode.get("research_profile")
        if isinstance(profile, str) and profile.strip():
            return profile.strip()
    task_contract = metadata.get("task_contract")
    if isinstance(task_contract, dict):
        profile = task_contract.get("research_profile")
        if isinstance(profile, str) and profile.strip():
            return profile.strip()
    return "medium"


def _max_subagent_requests(context: RunContext) -> int:
    metadata = context.run_input.tool_policy.metadata
    task_contract = metadata.get("task_contract")
    if isinstance(task_contract, dict):
        raw = task_contract.get("max_subagent_requests")
        if isinstance(raw, int):
            return max(0, raw)
    if not _deep_research_mode(context):
        return 10_000
    profile = _deep_research_profile(context)
    if profile == "light":
        return 0
    if profile == "hard":
        return 4
    return 2


def _planned_or_started_subagent_count(context: RunContext) -> int:
    count = 0
    planned = context.metadata.get("planned_subagent_group")
    if isinstance(planned, dict) and isinstance(planned.get("tasks"), list):
        count += len([item for item in planned["tasks"] if isinstance(item, dict)])
    runs = context.metadata.get("subagent_runs")
    if isinstance(runs, list):
        count += len([item for item in runs if isinstance(item, dict)])
    return count


def _deep_research_child_task(task: str) -> str:
    cleaned = _strip_child_file_write_instructions(task)
    return (
        f"{cleaned}\n\n"
        "Deep Research child constraints: return compact source notes only. "
        "Do not create, overwrite, or edit research/report.md, "
        "research/sources.jsonl, or any parent report artifact. The parent run "
        "owns the report and source ledger. Use web_search to find candidates, "
        "then web_fetch at most 3 best URLs when available. Include concrete "
        "URLs, whether each source was actually fetched/read, and any coverage "
        "gaps. Keep the final child answer under 1200 words."
    )


def _record_subagent_backpressure(
    context: RunContext, skipped: list[dict[str, object]]
) -> None:
    existing = context.metadata.get("subagent_backpressure")
    rows = (
        [item for item in existing if isinstance(item, dict)]
        if isinstance(existing, list)
        else []
    )
    rows.extend(skipped)
    context.metadata["subagent_backpressure"] = rows


def _strip_child_file_write_instructions(task: str) -> str:
    lines: list[str] = []
    replacement_added = False
    for line in task.splitlines():
        normalized = " ".join(line.lower().split())
        asks_to_write_file = (
            "write a summary to" in normalized
            or "save " in normalized
            or "write " in normalized
        ) and "research/" in normalized
        if asks_to_write_file:
            if not replacement_added:
                lines.append(
                    "- Return the summary in your final answer to the parent; "
                    "do not write files."
                )
                replacement_added = True
            continue
        lines.append(line)
    return "\n".join(lines).strip() or task.strip()


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
