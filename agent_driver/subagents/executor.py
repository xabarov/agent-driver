"""Subagent execution helpers without coupling to runtime mixins."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from inspect import signature
from typing import Callable
from uuid import uuid4

from agent_driver.contracts.artifacts import ArtifactRef
from agent_driver.contracts.enums import (
    ParentStateWriteMode,
    SubagentExecutionMode,
    SubagentGroupStatus,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.subagents import MergeProvenance, SubagentGroup, SubagentRun
from agent_driver.subagents.handoff import SubagentParentHandoff
from agent_driver.subagents.isolation import (
    ChildWorkspace,
    cleanup_child_workspace,
    prepare_child_workspace,
)
from agent_driver.subagents.join import evaluate_join_policy
from agent_driver.subagents.merge import merge_subagent_outputs
from agent_driver.subagents.planner import build_child_context_handoff
from agent_driver.subagents.specs import SubagentGroupSpec, SubagentTaskSpec
from agent_driver.subagents.store import SubagentStore
from agent_driver.subagents.workers import apply_worker_tool_surface

ChildRunner = Callable[[AgentRunInput], "object"]

# Optional observability callback for group-level transitions (P3a H11).
# Hosts pass this in when they want SUBAGENT_* runtime events surfaced
# without taking ownership of the executor signature. Failure to invoke
# (callback raises, or callback is None) must never break execution —
# callers depend on the result envelope, not the event stream.
SubagentEventCallback = Callable[[str, dict], None]


@dataclass(frozen=True, slots=True)
class SubagentExecutionResult:
    """Subagent group execution result for parent runtime."""

    group: SubagentGroup
    runs: list[SubagentRun]
    join_state: str
    merged_summary: str


_TERMINAL_STATUSES = {
    SubagentStatus.COMPLETED,
    SubagentStatus.FAILED,
    SubagentStatus.CANCELLED,
    SubagentStatus.TIMED_OUT,
}
_MAX_CHILD_OUTPUT_ARTIFACT_REFS = 8


def _safe_emit(
    on_event: "SubagentEventCallback | None", event_type: str, payload: dict
) -> None:
    """Invoke ``on_event`` defensively — observability must never break exec."""
    if on_event is None:
        return
    try:
        on_event(event_type, payload)
    except Exception:  # pragma: no cover — host bug must not abort group
        pass


def _status_from_output(
    output: AgentRunOutput,
) -> tuple[SubagentStatus, SubagentTerminalState]:
    if output.status.value == "completed":
        return SubagentStatus.COMPLETED, SubagentTerminalState.SUCCEEDED
    if output.status.value == "timed_out":
        return SubagentStatus.TIMED_OUT, SubagentTerminalState.TIMED_OUT
    if output.status.value == "cancelled":
        return SubagentStatus.CANCELLED, SubagentTerminalState.CANCELLED
    return SubagentStatus.FAILED, SubagentTerminalState.FAILED


async def _run_single_child_task(
    *,
    parent: SubagentParentHandoff,
    group: SubagentGroup,
    task: SubagentTaskSpec,
    idx: int,
    store: SubagentStore,
    child_runner: ChildRunner,
    child_app_metadata: dict | None,
    parent_abort_handle: object | None,
) -> SubagentRun:
    child_abort_handle = (
        parent_abort_handle.child()
        if parent_abort_handle is not None and hasattr(parent_abort_handle, "child")
        else parent_abort_handle
    )
    handoff, handoff_audit = build_child_context_handoff(
        task=task,
        parent_summary=parent.answer or "",
        artifact_refs=parent.artifact_refs,
        digest_refs=parent.digest_refs,
        planning_state=parent.planning_state,
    )
    pending = store.upsert_run(
        SubagentRun(
            subagent_run_id=f"sub_{uuid4().hex[:12]}",
            parent_run_id=parent.run_id,
            parent_attempt_id=parent.attempt_id,
            task_id=task.task_id,
            task_type="subagent_task",
            description=task.description,
            execution_mode=SubagentExecutionMode.SYNC,
            fanout_slot=idx,
            status=SubagentStatus.RUNNING,
            metadata={"handoff": handoff, "handoff_audit": handoff_audit},
        ),
        idempotency_key=task.idempotency_key,
    )
    if bool(getattr(child_abort_handle, "is_aborted", False)):
        cancelled = _cancelled_child_run(
            pending=pending,
            task=task,
            idx=idx,
            reason=str(getattr(child_abort_handle, "reason", None) or "parent_aborted"),
        )
        store.upsert_run(cancelled, idempotency_key=task.idempotency_key)
        return cancelled
    workspace = prepare_child_workspace(
        parent_workspace_cwd=parent.workspace_cwd,
        task_metadata=task.metadata,
    )
    try:
        child_input = AgentRunInput(
            input=task.task,
            run_id=f"child_{uuid4().hex[:12]}",
            thread_id=parent.thread_id,
            agent_id=f"{parent.agent_id}.child",
            graph_preset=parent.graph_preset,
            model_role=parent.model_role,
            agent_profile=task.profile,
            tool_policy=_child_tool_policy(parent=parent, task=task),
            deadline_seconds=task.deadline_seconds,
            app_metadata=_child_app_metadata(
                parent=parent,
                group=group,
                child_app_metadata=child_app_metadata,
                workspace=workspace,
            ),
        )
        output_any = _call_child_runner(
            child_runner,
            child_input,
            child_abort_handle=child_abort_handle,
        )
        output = await output_any if hasattr(output_any, "__await__") else output_any
        if not isinstance(output, AgentRunOutput):
            raise RuntimeError("child_runner must return AgentRunOutput")
    finally:
        cleanup_child_workspace(workspace)
    run_status, terminal_state = _status_from_output(output)
    artifact_refs = _bounded_output_artifact_refs(output)
    merge_provenance = (
        MergeProvenance(
            strategy="child_output",
            source_kind="child_run",
            carried_keys=_carried_child_keys(artifact_refs),
            parent_state_write=ParentStateWriteMode.BOUNDED_APPEND_ONLY,
            metadata={"child_run_id": output.run_id},
        )
        if run_status == SubagentStatus.COMPLETED
        else None
    )
    completed = SubagentRun(
        subagent_run_id=pending.subagent_run_id,
        parent_run_id=parent.run_id,
        parent_attempt_id=parent.attempt_id,
        child_run_id=output.run_id,
        task_id=task.task_id,
        task_type="subagent_task",
        description=task.description,
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=idx,
        status=run_status,
        terminal_state=terminal_state,
        latency_ms=None,
        tokens=output.usage,
        output_pointer=_first_output_artifact(output),
        merge_provenance=merge_provenance,
        metadata={
            **pending.metadata,
            "summary": output.answer or "",
            "child_artifact_refs": artifact_refs,
            "child_artifact_audit": _child_artifact_audit(output, artifact_refs),
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
        },
    )
    store.upsert_run(completed, idempotency_key=task.idempotency_key)
    return completed


def _call_child_runner(
    child_runner: ChildRunner,
    child_input: AgentRunInput,
    *,
    child_abort_handle: object | None,
) -> object:
    if child_abort_handle is None:
        return child_runner(child_input)
    try:
        runner_signature = signature(child_runner)
    except (TypeError, ValueError):
        return child_runner(child_input)
    if "abort_handle" not in runner_signature.parameters:
        return child_runner(child_input)
    return child_runner(child_input, abort_handle=child_abort_handle)


def _bounded_output_artifact_refs(
    output: AgentRunOutput, *, max_refs: int = _MAX_CHILD_OUTPUT_ARTIFACT_REFS
) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in output.artifacts[:max_refs]]


def _first_output_artifact(output: AgentRunOutput) -> ArtifactRef | None:
    return output.artifacts[0] if output.artifacts else None


def _child_artifact_audit(
    output: AgentRunOutput, artifact_refs: list[dict[str, object]]
) -> dict[str, int]:
    return {
        "artifact_refs_in": len(output.artifacts),
        "artifact_refs_kept": len(artifact_refs),
        "dropped_artifacts": max(0, len(output.artifacts) - len(artifact_refs)),
    }


def _carried_child_keys(artifact_refs: list[dict[str, object]]) -> list[str]:
    keys = ["summary"]
    if artifact_refs:
        keys.append("artifact_refs")
    return keys


def _child_tool_policy(
    *, parent: SubagentParentHandoff, task: SubagentTaskSpec
) -> dict[str, object]:
    worker_type = task.metadata.get("worker_type") or task.metadata.get("role")
    return apply_worker_tool_surface(
        parent_tool_policy=parent.tool_policy,
        worker_type=str(worker_type) if worker_type is not None else None,
    )


def _child_app_metadata(
    *,
    parent: SubagentParentHandoff,
    group: SubagentGroup,
    child_app_metadata: dict | None,
    workspace: ChildWorkspace,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "parent_run_id": parent.run_id,
        "subagent_group_id": group.group_id,
        **(child_app_metadata or {}),
    }
    if workspace.cwd is not None:
        metadata["workspace_cwd"] = str(workspace.cwd)
        metadata["workspace_cwd_source"] = workspace.mode
    return metadata


def _child_abort_handle(parent_abort_handle: object | None) -> object | None:
    if parent_abort_handle is not None and hasattr(parent_abort_handle, "child"):
        return parent_abort_handle.child()
    return parent_abort_handle


def _build_pending_child_run(
    *,
    parent: SubagentParentHandoff,
    task: SubagentTaskSpec,
    idx: int,
    execution_mode: SubagentExecutionMode,
    child_run_id: str | None = None,
) -> SubagentRun:
    handoff, handoff_audit = build_child_context_handoff(
        task=task,
        parent_summary=parent.answer or "",
        artifact_refs=parent.artifact_refs,
        digest_refs=parent.digest_refs,
        planning_state=parent.planning_state,
    )
    return SubagentRun(
        subagent_run_id=f"sub_{uuid4().hex[:12]}",
        parent_run_id=parent.run_id,
        parent_attempt_id=parent.attempt_id,
        child_run_id=child_run_id,
        task_id=task.task_id,
        task_type="subagent_task",
        description=task.description,
        execution_mode=execution_mode,
        fanout_slot=idx,
        status=SubagentStatus.RUNNING,
        metadata={"handoff": handoff, "handoff_audit": handoff_audit},
    )


def _build_child_input(
    *,
    parent: SubagentParentHandoff,
    group: SubagentGroup,
    task: SubagentTaskSpec,
    child_run_id: str,
    child_app_metadata: dict | None,
    workspace: ChildWorkspace,
) -> AgentRunInput:
    return AgentRunInput(
        input=task.task,
        run_id=child_run_id,
        thread_id=parent.thread_id,
        agent_id=f"{parent.agent_id}.child",
        graph_preset=parent.graph_preset,
        model_role=parent.model_role,
        agent_profile=task.profile,
        tool_policy=_child_tool_policy(parent=parent, task=task),
        deadline_seconds=task.deadline_seconds,
        app_metadata=_child_app_metadata(
            parent=parent,
            group=group,
            child_app_metadata=child_app_metadata,
            workspace=workspace,
        ),
    )


def _cancelled_child_run(
    *,
    pending: SubagentRun,
    task: SubagentTaskSpec,
    idx: int,
    reason: str,
) -> SubagentRun:
    return SubagentRun(
        subagent_run_id=pending.subagent_run_id,
        parent_run_id=pending.parent_run_id,
        parent_attempt_id=pending.parent_attempt_id,
        parent_checkpoint_id=pending.parent_checkpoint_id,
        child_run_id=None,
        task_id=task.task_id,
        task_type="subagent_task",
        description=task.description,
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=idx,
        status=SubagentStatus.CANCELLED,
        terminal_state=SubagentTerminalState.CANCELLED,
        metadata={
            **pending.metadata,
            "status": "cancelled",
            "terminal_reason": reason,
        },
    )


def _select_schedulable_tasks(
    *,
    group_spec: SubagentGroupSpec,
    max_child_runs: int,
) -> tuple[list[SubagentTaskSpec], dict[str, object]]:
    """Apply deterministic group scheduling limits before child execution."""
    max_parallel = (
        max(0, group_spec.max_parallel)
        if group_spec.max_parallel is not None
        else max_child_runs
    )
    slot_limit = max(0, min(max_child_runs, max_parallel))
    token_remaining = group_spec.token_budget
    cost_remaining = group_spec.cost_budget_usd
    scheduled: list[SubagentTaskSpec] = []
    skipped: list[dict[str, object]] = []
    for task in group_spec.tasks:
        if len(scheduled) >= slot_limit:
            skipped.append({"task_id": task.task_id, "reason": "parallel_limit"})
            continue
        task_tokens = task.token_budget or 0
        if token_remaining is not None and task_tokens > token_remaining:
            skipped.append({"task_id": task.task_id, "reason": "token_budget"})
            continue
        task_cost = task.cost_budget_usd or 0.0
        if cost_remaining is not None and task_cost > cost_remaining:
            skipped.append({"task_id": task.task_id, "reason": "cost_budget"})
            continue
        scheduled.append(task)
        if token_remaining is not None:
            token_remaining -= task_tokens
        if cost_remaining is not None:
            cost_remaining -= task_cost
    return scheduled, {
        "scheduled_tasks": len(scheduled),
        "backpressure_skipped_tasks": skipped,
        "token_budget_remaining": token_remaining,
        "cost_budget_usd_remaining": cost_remaining,
    }


def _task_role(task: SubagentTaskSpec) -> str:
    role = task.metadata.get("worker_type") or task.metadata.get("role")
    return str(role) if role is not None else ""


async def execute_subagent_group_sync(
    *,
    parent: SubagentParentHandoff,
    group_spec: SubagentGroupSpec,
    store: SubagentStore,
    child_runner: ChildRunner,
    max_child_runs: int,
    child_app_metadata: dict | None = None,
    on_event: SubagentEventCallback | None = None,
    parent_abort_handle: object | None = None,
) -> SubagentExecutionResult:
    """Execute child tasks synchronously and persist group/run rows.

    ``on_event`` (optional) is invoked for group + child transitions with
    transport-neutral payloads so callers can fan out to SSE / Phoenix /
    custom sinks without coupling the executor to a specific delivery
    mechanism. Emission is best-effort — exceptions raised by the callback
    are swallowed so observability glitches cannot abort the group.

    Event types (correspond 1:1 to ``RuntimeEventType.SUBAGENT_*``):

    * ``subagent_group_started`` — once at group entry. Payload has
      ``group_id``, ``task_count``, ``join_policy``, ``merge_mode``.
    * ``subagent_started`` — before each child task runs. Payload has
      ``group_id``, ``index``, ``task_id``, ``role`` (when set).
    * ``subagent_completed`` — after each child task. Payload has
      ``group_id``, ``index``, ``task_id``, ``status`` (str), ``role``.
    * ``subagent_group_joined`` — at successful join (``done=True``).
    * ``subagent_group_failed`` — when join_state is not "done"
      (waiting / cancelled). Payload carries ``join_state`` so consumers
      can distinguish.
    """
    limited_tasks, scheduling_metadata = _select_schedulable_tasks(
        group_spec=group_spec,
        max_child_runs=max_child_runs,
    )
    _safe_emit(
        on_event,
        "subagent_group_started",
        {
            "group_id": group_spec.group_id,
            "task_count": len(limited_tasks),
            "join_policy": (
                group_spec.join_policy.value
                if hasattr(group_spec.join_policy, "value")
                else str(group_spec.join_policy)
            ),
            "merge_mode": (
                group_spec.merge_mode.value
                if hasattr(group_spec.merge_mode, "value")
                else str(group_spec.merge_mode)
            ),
        },
    )
    group = store.upsert_group(
        SubagentGroup(
            group_id=group_spec.group_id,
            parent_run_id=parent.run_id,
            parent_attempt_id=parent.attempt_id,
            purpose=group_spec.purpose,
            join_policy=group_spec.join_policy,
            merge_mode=group_spec.merge_mode,
            max_parallel=group_spec.max_parallel,
            deadline_seconds=group_spec.deadline_seconds,
            token_budget=group_spec.token_budget,
            cost_budget_usd=group_spec.cost_budget_usd,
            status=SubagentGroupStatus.RUNNING,
            metadata={
                **group_spec.metadata,
                "requested_tasks": len(group_spec.tasks),
                **scheduling_metadata,
            },
        )
    )
    child_runs: list[SubagentRun] = []
    for idx, task in enumerate(limited_tasks, start=1):
        _safe_emit(
            on_event,
            "subagent_started",
            {
                "group_id": group_spec.group_id,
                "index": idx,
                "task_id": task.task_id,
                "role": _task_role(task),
            },
        )
        completed = await _run_single_child_task(
            parent=parent,
            group=group,
            task=task,
            idx=idx,
            store=store,
            child_runner=child_runner,
            child_app_metadata=child_app_metadata,
            parent_abort_handle=parent_abort_handle,
        )
        child_runs.append(completed)
        _safe_emit(
            on_event,
            "subagent_completed",
            {
                "group_id": group_spec.group_id,
                "index": idx,
                "task_id": task.task_id,
                "role": _task_role(task),
                "subagent_run_id": completed.subagent_run_id,
                "child_run_id": completed.child_run_id,
                "status": (
                    completed.status.value
                    if hasattr(completed.status, "value")
                    else str(completed.status)
                ),
            },
        )
    join_decision = evaluate_join_policy(
        join_policy=group_spec.join_policy,
        runs=child_runs,
        k=min(2, len(child_runs)) if len(child_runs) > 1 else 1,
        deadline_reached=True,
    )
    merged_summary, provenance = merge_subagent_outputs(
        merge_mode=group_spec.merge_mode,
        runs=child_runs,
    )
    group = store.upsert_group(
        group.model_copy(
            update={
                "status": (
                    SubagentGroupStatus.COMPLETED
                    if join_decision.done
                    else SubagentGroupStatus.RUNNING
                ),
                "child_run_ids": [item.child_run_id or "" for item in child_runs],
                "merge_provenance": provenance,
                "metadata": {
                    **group.metadata,
                    "join_state": join_decision.state,
                    "completed_ids": list(join_decision.completed_ids),
                    "failed_ids": list(join_decision.failed_ids),
                    "cancelled_ids": list(join_decision.cancelled_ids),
                },
            }
        )
    )
    _safe_emit(
        on_event,
        "subagent_group_joined" if join_decision.done else "subagent_group_failed",
        {
            "group_id": group_spec.group_id,
            "join_state": join_decision.state,
            "completed_count": len(join_decision.completed_ids),
            "failed_count": len(join_decision.failed_ids),
            "cancelled_count": len(join_decision.cancelled_ids),
        },
    )
    return SubagentExecutionResult(
        group=group,
        runs=child_runs,
        join_state=join_decision.state,
        merged_summary=merged_summary,
    )


async def execute_subagent_group_background(
    *,
    parent: SubagentParentHandoff,
    group_spec: SubagentGroupSpec,
    store: SubagentStore,
    child_runner: ChildRunner,
    max_child_runs: int,
    child_app_metadata: dict | None = None,
    on_event: SubagentEventCallback | None = None,
    parent_abort_handle: object | None = None,
) -> SubagentExecutionResult:
    """Schedule child tasks in the current event loop and return immediately."""
    limited_tasks, scheduling_metadata = _select_schedulable_tasks(
        group_spec=group_spec,
        max_child_runs=max_child_runs,
    )
    _safe_emit(
        on_event,
        "subagent_group_started",
        {
            "group_id": group_spec.group_id,
            "task_count": len(limited_tasks),
            "join_policy": (
                group_spec.join_policy.value
                if hasattr(group_spec.join_policy, "value")
                else str(group_spec.join_policy)
            ),
            "merge_mode": (
                group_spec.merge_mode.value
                if hasattr(group_spec.merge_mode, "value")
                else str(group_spec.merge_mode)
            ),
            "execution_mode": SubagentExecutionMode.BACKGROUND.value,
        },
    )
    group = store.upsert_group(
        SubagentGroup(
            group_id=group_spec.group_id,
            parent_run_id=parent.run_id,
            parent_attempt_id=parent.attempt_id,
            purpose=group_spec.purpose,
            join_policy=group_spec.join_policy,
            merge_mode=group_spec.merge_mode,
            max_parallel=group_spec.max_parallel,
            deadline_seconds=group_spec.deadline_seconds,
            token_budget=group_spec.token_budget,
            cost_budget_usd=group_spec.cost_budget_usd,
            status=SubagentGroupStatus.RUNNING,
            metadata={
                **group_spec.metadata,
                "execution_mode": "asyncio_background",
                "requested_tasks": len(group_spec.tasks),
                **scheduling_metadata,
            },
        )
    )
    pending_runs: list[SubagentRun] = []
    for idx, task in enumerate(limited_tasks, start=1):
        child_run_id = f"child_{uuid4().hex[:12]}"
        pending = store.upsert_run(
            _build_pending_child_run(
                parent=parent,
                task=task,
                idx=idx,
                execution_mode=SubagentExecutionMode.BACKGROUND,
                child_run_id=child_run_id,
            ),
            idempotency_key=task.idempotency_key,
        )
        pending_runs.append(pending)
        _safe_emit(
            on_event,
            "subagent_started",
            {
                "group_id": group_spec.group_id,
                "index": idx,
                "task_id": task.task_id,
                "role": _task_role(task),
                "subagent_run_id": pending.subagent_run_id,
                "child_run_id": child_run_id,
                "execution_mode": SubagentExecutionMode.BACKGROUND.value,
            },
        )
        workspace = prepare_child_workspace(
            parent_workspace_cwd=parent.workspace_cwd,
            task_metadata=task.metadata,
        )
        child_input = _build_child_input(
            parent=parent,
            group=group,
            task=task,
            child_run_id=child_run_id,
            child_app_metadata={
                "subagent_execution_mode": "asyncio_background",
                **(child_app_metadata or {}),
            },
            workspace=workspace,
        )
        asyncio.create_task(
            _complete_background_child_task(
                parent=parent,
                group=group,
                task=task,
                idx=idx,
                pending=pending,
                store=store,
                child_runner=child_runner,
                child_input=child_input,
                child_abort_handle=_child_abort_handle(parent_abort_handle),
                idempotency_key=task.idempotency_key,
                on_event=on_event,
                workspace=workspace,
            )
        )
    group = store.upsert_group(
        group.model_copy(
            update={
                "child_run_ids": [item.child_run_id or "" for item in pending_runs],
                "metadata": {
                    **group.metadata,
                    "scheduled_subagent_run_ids": [
                        item.subagent_run_id for item in pending_runs
                    ],
                    "join_state": "background_running",
                },
            }
        )
    )
    return SubagentExecutionResult(
        group=group,
        runs=pending_runs,
        join_state="background_running",
        merged_summary="",
    )


async def _complete_background_child_task(
    *,
    parent: SubagentParentHandoff,
    group: SubagentGroup,
    task: SubagentTaskSpec,
    idx: int,
    pending: SubagentRun,
    store: SubagentStore,
    child_runner: ChildRunner,
    child_input: AgentRunInput,
    child_abort_handle: object | None,
    idempotency_key: str | None,
    on_event: SubagentEventCallback | None,
    workspace: ChildWorkspace,
) -> None:
    try:
        if bool(getattr(child_abort_handle, "is_aborted", False)):
            completed = _cancelled_child_run(
                pending=pending,
                task=task,
                idx=idx,
                reason=str(
                    getattr(child_abort_handle, "reason", None) or "parent_aborted"
                ),
            )
        else:
            try:
                output_any = _call_child_runner(
                    child_runner,
                    child_input,
                    child_abort_handle=child_abort_handle,
                )
                output = (
                    await output_any if hasattr(output_any, "__await__") else output_any
                )
                if not isinstance(output, AgentRunOutput):
                    raise RuntimeError("child_runner must return AgentRunOutput")
                completed = _completed_child_run_from_output(
                    parent=parent,
                    pending=pending,
                    task=task,
                    idx=idx,
                    output=output,
                    execution_mode=SubagentExecutionMode.BACKGROUND,
                )
            except Exception as exc:  # pragma: no cover - defensive task boundary
                completed = _failed_child_run(
                    pending=pending,
                    task=task,
                    idx=idx,
                    reason=type(exc).__name__,
                )
    finally:
        cleanup_child_workspace(workspace)
    store.upsert_run(completed, idempotency_key=idempotency_key)
    _safe_emit(
        on_event,
        "subagent_completed",
        {
            "group_id": group.group_id,
            "index": idx,
            "task_id": task.task_id,
            "role": _task_role(task),
            "subagent_run_id": completed.subagent_run_id,
            "child_run_id": completed.child_run_id,
            "status": (
                completed.status.value
                if hasattr(completed.status, "value")
                else str(completed.status)
            ),
            "execution_mode": SubagentExecutionMode.BACKGROUND.value,
        },
    )
    _maybe_complete_background_group(store=store, group=group)


def _completed_child_run_from_output(
    *,
    parent: SubagentParentHandoff,
    pending: SubagentRun,
    task: SubagentTaskSpec,
    idx: int,
    output: AgentRunOutput,
    execution_mode: SubagentExecutionMode,
) -> SubagentRun:
    run_status, terminal_state = _status_from_output(output)
    artifact_refs = _bounded_output_artifact_refs(output)
    merge_provenance = (
        MergeProvenance(
            strategy="child_output",
            source_kind="child_run",
            carried_keys=_carried_child_keys(artifact_refs),
            parent_state_write=ParentStateWriteMode.BOUNDED_APPEND_ONLY,
            metadata={"child_run_id": output.run_id},
        )
        if run_status == SubagentStatus.COMPLETED
        else None
    )
    return SubagentRun(
        subagent_run_id=pending.subagent_run_id,
        parent_run_id=parent.run_id,
        parent_attempt_id=parent.attempt_id,
        child_run_id=output.run_id,
        task_id=task.task_id,
        task_type="subagent_task",
        description=task.description,
        execution_mode=execution_mode,
        fanout_slot=idx,
        status=run_status,
        terminal_state=terminal_state,
        latency_ms=None,
        tokens=output.usage,
        output_pointer=_first_output_artifact(output),
        merge_provenance=merge_provenance,
        metadata={
            **pending.metadata,
            "summary": output.answer or "",
            "child_artifact_refs": artifact_refs,
            "child_artifact_audit": _child_artifact_audit(output, artifact_refs),
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
        },
    )


def _failed_child_run(
    *,
    pending: SubagentRun,
    task: SubagentTaskSpec,
    idx: int,
    reason: str,
) -> SubagentRun:
    return SubagentRun(
        subagent_run_id=pending.subagent_run_id,
        parent_run_id=pending.parent_run_id,
        parent_attempt_id=pending.parent_attempt_id,
        parent_checkpoint_id=pending.parent_checkpoint_id,
        child_run_id=pending.child_run_id,
        task_id=task.task_id,
        task_type="subagent_task",
        description=task.description,
        execution_mode=SubagentExecutionMode.BACKGROUND,
        fanout_slot=idx,
        status=SubagentStatus.FAILED,
        terminal_state=SubagentTerminalState.FAILED,
        failure_code=reason,
        metadata={**pending.metadata, "status": "failed", "terminal_reason": reason},
    )


def _maybe_complete_background_group(
    *, store: SubagentStore, group: SubagentGroup
) -> None:
    current_group = next(
        (
            row
            for row in store.list_groups(group.parent_run_id)
            if row.group_id == group.group_id
        ),
        group,
    )
    scheduled_ids = current_group.metadata.get("scheduled_subagent_run_ids")
    if not isinstance(scheduled_ids, list) or not scheduled_ids:
        return
    runs = store.list_runs(group.parent_run_id)
    rows_by_id = {row.subagent_run_id: row for row in runs}
    scheduled_rows = [
        rows_by_id.get(str(subagent_run_id)) for subagent_run_id in scheduled_ids
    ]
    if any(
        row is None or row.status not in _TERMINAL_STATUSES for row in scheduled_rows
    ):
        return
    store.upsert_group(
        current_group.model_copy(
            update={
                "status": SubagentGroupStatus.COMPLETED,
                "child_run_ids": [
                    row.child_run_id or "" for row in scheduled_rows if row is not None
                ],
                "metadata": {
                    **current_group.metadata,
                    "join_state": "background_completed",
                },
            }
        )
    )


__all__ = [
    "SubagentEventCallback",
    "SubagentExecutionResult",
    "execute_subagent_group_background",
    "execute_subagent_group_sync",
]
