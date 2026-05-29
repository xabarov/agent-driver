"""Subagent fan-out execution after tool stage."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from agent_driver.contracts.control import ControlKind, ControlPriority, ControlRequest
from agent_driver.contracts.enums import (
    RuntimeEventType,
    SubagentJoinPolicy,
    SubagentMergeMode,
)
from agent_driver.contracts.subagent_mailbox import (
    SubagentMailboxDirection,
    SubagentMailboxItem,
    SubagentMailboxKind,
)
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig, RunnerDeps
from agent_driver.subagents import (
    SubagentGroupSpec,
    SubagentTaskSpec,
    execute_subagent_group_sync,
    summarize_child_runs_for_parent,
)
from agent_driver.subagents.handoff import SubagentParentHandoff


class SubagentStageHost(Protocol):
    """Host surface for subagent group execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: object) -> None: ...
    def run(self, run_input: Any) -> Any: ...


async def maybe_execute_subagent_group(
    host: SubagentStageHost, context: RunContext
) -> None:
    """Execute sync subagent group under feature flag."""
    if not host._config.enable_subagents:
        return
    if context.metadata.get("subagent_origin") == "child":
        return
    policy_metadata = context.run_input.tool_policy.metadata
    planned = None
    if isinstance(policy_metadata, dict):
        planned = policy_metadata.get("planned_subagent_group")
    if not isinstance(planned, dict):
        planned = context.metadata.get("planned_subagent_group")
    if not isinstance(planned, dict):
        return
    group_spec = _group_spec_from_planned(planned, max_child_runs=host._config.max_child_runs)
    if group_spec is None:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.SUBAGENT_GROUP_STARTED,
        payload={"group_id": group_spec.group_id, "tasks": len(group_spec.tasks)},
    )
    parent = SubagentParentHandoff(
        run_id=context.run_id,
        attempt_id=context.attempt_id,
        thread_id=context.run_input.thread_id,
        agent_id=context.run_input.agent_id,
        graph_preset=context.run_input.graph_preset,
        model_role=context.run_input.model_role,
        tool_policy=context.run_input.tool_policy.model_dump(mode="json"),
        answer=context.llm_response.message.content if context.llm_response else None,
        artifact_refs=_list_metadata(context, "artifact_refs"),
        digest_refs=_list_metadata(context, "digest_refs"),
        planning_state=_dict_metadata(context, "planning_state"),
    )
    result = await execute_subagent_group_sync(
        parent=parent,
        group_spec=group_spec,
        store=host._deps.subagent_store,
        child_runner=host.run,
        max_child_runs=host._config.max_child_runs,
        child_app_metadata={"subagent_origin": "child"},
        on_event=lambda event_type, payload: _emit_child_subagent_event(
            host, context, event_type, payload
        ),
        parent_abort_handle=context.abort_handle,
    )
    context.metadata["subagent_groups"] = [
        row.model_dump(mode="json")
        for row in host._deps.subagent_store.list_groups(context.run_id)
    ]
    context.metadata["subagent_runs"] = summarize_child_runs_for_parent(
        [
            row.model_dump(mode="json")
            for row in host._deps.subagent_store.list_runs(context.run_id)
        ]
    )
    context.metadata["subagent_merge_summary"] = result.merged_summary
    context.metadata.pop("planned_subagent_group", None)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.SUBAGENT_GROUP_JOINED,
        payload={
            "group_id": result.group.group_id,
            "join_state": result.join_state,
            "child_runs": len(result.runs),
        },
    )


def _group_spec_from_planned(
    planned: dict[str, object], *, max_child_runs: int
) -> SubagentGroupSpec | None:
    tasks_payload = planned.get("tasks")
    if not isinstance(tasks_payload, list) or not tasks_payload:
        return None
    task_specs = []
    for item in tasks_payload[:max_child_runs]:
        if not isinstance(item, dict):
            continue
        task_specs.append(
            SubagentTaskSpec(
                task_id=str(item.get("task_id", f"task_{uuid4().hex[:8]}")),
                task=str(item.get("task", "")),
                description=str(item.get("description", "subagent task")),
                idempotency_key=(
                    str(item.get("idempotency_key"))
                    if item.get("idempotency_key") is not None
                    else None
                ),
            )
        )
    if not task_specs:
        return None
    return SubagentGroupSpec(
        group_id=str(planned.get("group_id", f"group_{uuid4().hex[:8]}")),
        purpose=str(planned.get("purpose", "parent_fanout")),
        join_policy=SubagentJoinPolicy(str(planned.get("join_policy", "wait_all"))),
        merge_mode=SubagentMergeMode(str(planned.get("merge_mode", "append"))),
        tasks=tuple(task_specs),
        max_parallel=planned.get("max_parallel"),
        deadline_seconds=planned.get("deadline_seconds"),
        token_budget=planned.get("token_budget"),
        cost_budget_usd=planned.get("cost_budget_usd"),
        metadata={"origin": "runtime_metadata"},
    )


def _list_metadata(context: RunContext, key: str) -> list[dict[str, object]]:
    value = context.metadata.get(key, [])
    return value if isinstance(value, list) else []


def _dict_metadata(context: RunContext, key: str) -> dict[str, object] | None:
    value = context.metadata.get(key)
    return value if isinstance(value, dict) else None


def _emit_child_subagent_event(
    host: SubagentStageHost,
    context: RunContext,
    event_type: str,
    payload: dict[str, object],
) -> None:
    mapping = {
        RuntimeEventType.SUBAGENT_STARTED.value: RuntimeEventType.SUBAGENT_STARTED,
        RuntimeEventType.SUBAGENT_COMPLETED.value: RuntimeEventType.SUBAGENT_COMPLETED,
        RuntimeEventType.SUBAGENT_SPAWNED.value: RuntimeEventType.SUBAGENT_SPAWNED,
    }
    runtime_type = mapping.get(event_type)
    if runtime_type is None:
        return
    emit_step_event(
        host,
        context,
        event_type=runtime_type,
        payload=payload,
    )
    if runtime_type == RuntimeEventType.SUBAGENT_COMPLETED:
        _queue_child_completion_notification(host, context, payload)


def _queue_child_completion_notification(
    host: SubagentStageHost,
    context: RunContext,
    payload: dict[str, object],
) -> None:
    """Queue child completion as a deferred parent steering notification."""
    task_id = str(payload.get("task_id") or "subagent")
    status = str(payload.get("status") or "unknown")
    subagent_run_id = _optional_text(payload.get("subagent_run_id"))
    child_run_id = _optional_text(payload.get("child_run_id"))
    message = f"Subagent task `{task_id}` finished with status `{status}`."
    dedupe_key = "|".join(
        [
            "subagent_completed",
            str(payload.get("group_id") or ""),
            subagent_run_id or task_id,
            status,
        ]
    )
    if host._deps.subagent_mailbox_store is not None:
        host._deps.subagent_mailbox_store.enqueue(
            SubagentMailboxItem(
                parent_run_id=context.run_id,
                direction=SubagentMailboxDirection.CHILD_TO_PARENT,
                kind=SubagentMailboxKind.TASK_NOTIFICATION,
                subagent_run_id=subagent_run_id,
                child_run_id=child_run_id,
                group_id=_optional_text(payload.get("group_id")),
                payload={"message": message, "status": status, "task_id": task_id},
                source="subagent_runtime",
                dedupe_key=dedupe_key,
            )
        )
    if host._deps.command_queue_store is None:
        return
    queued = host._deps.command_queue_store.enqueue(
        ControlRequest(
            kind=ControlKind.ENQUEUE_USER_MESSAGE,
            run_id=context.run_id,
            thread_id=context.run_input.thread_id,
            agent_id=context.run_input.agent_id,
            priority=ControlPriority.LATER,
            payload={
                "message": message,
                "subagent_run_id": subagent_run_id,
                "child_run_id": child_run_id,
                "status": status,
            },
            source="subagent_notification",
            dedupe_key=dedupe_key,
        )
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.COMMAND_QUEUED,
        payload={
            "queue_id": queued.queue_id,
            "kind": queued.kind.value,
            "priority": queued.priority.value,
            "source": queued.source,
        },
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


__all__ = ["SubagentStageHost", "maybe_execute_subagent_group"]
