"""Sync subagent execution helpers without coupling to runtime mixins."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from typing import Callable
from uuid import uuid4

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
from agent_driver.subagents.join import evaluate_join_policy
from agent_driver.subagents.merge import merge_subagent_outputs
from agent_driver.subagents.planner import build_child_context_handoff
from agent_driver.subagents.specs import SubagentGroupSpec, SubagentTaskSpec
from agent_driver.subagents.store import SubagentStore

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
    child_input = AgentRunInput(
        input=task.task,
        run_id=f"child_{uuid4().hex[:12]}",
        thread_id=parent.thread_id,
        agent_id=f"{parent.agent_id}.child",
        graph_preset=parent.graph_preset,
        model_role=parent.model_role,
        agent_profile=task.profile,
        tool_policy=parent.tool_policy,
        deadline_seconds=task.deadline_seconds,
        app_metadata={
            "parent_run_id": parent.run_id,
            "subagent_group_id": group.group_id,
            **(child_app_metadata or {}),
        },
    )
    output_any = _call_child_runner(
        child_runner,
        child_input,
        child_abort_handle=child_abort_handle,
    )
    output = await output_any if hasattr(output_any, "__await__") else output_any
    if not isinstance(output, AgentRunOutput):
        raise RuntimeError("child_runner must return AgentRunOutput")
    run_status, terminal_state = _status_from_output(output)
    merge_provenance = (
        MergeProvenance(
            strategy="child_output",
            source_kind="child_run",
            carried_keys=["summary"],
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
        merge_provenance=merge_provenance,
        metadata={
            "summary": output.answer or "",
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
        metadata={"status": "cancelled", "terminal_reason": reason},
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
                "role": getattr(task, "role", None) or "",
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
                "role": getattr(task, "role", None) or "",
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


__all__ = [
    "SubagentEventCallback",
    "SubagentExecutionResult",
    "execute_subagent_group_sync",
]
