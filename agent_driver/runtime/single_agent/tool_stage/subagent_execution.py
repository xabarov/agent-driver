"""Subagent fan-out execution after tool stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from agent_driver.contracts.control import ControlKind, ControlPriority, ControlRequest
from agent_driver.contracts.enums import (
    AgentProfile,
    RuntimeEventType,
    SubagentJoinPolicy,
    SubagentMergeMode,
)
from agent_driver.contracts.subagent_mailbox import (
    SubagentMailboxDirection,
    SubagentMailboxItem,
    SubagentMailboxKind,
)
from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    build_research_session_contract_from_context,
)
from agent_driver.runtime.metadata_state import (
    get_loop_control_state,
    get_research_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig, RunnerDeps
from agent_driver.subagents import (
    SubagentGroupSpec,
    SubagentTaskSpec,
    execute_subagent_group_background,
    execute_subagent_group_sync,
    summarize_child_runs_for_parent,
)
from agent_driver.subagents.handoff import SubagentParentHandoff


class SubagentStageHost(Protocol):
    """Host surface for subagent group execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: object) -> None: ...
    def run(self, run_input: Any) -> Any:
        """Execute a child run through the host runner."""
        raise NotImplementedError


def _host_deps(host: SubagentStageHost) -> RunnerDeps:
    return cast(RunnerDeps, getattr(host, "_deps"))


def _host_config(host: SubagentStageHost) -> RunnerConfig:
    return cast(RunnerConfig, getattr(host, "_config"))


async def maybe_execute_subagent_group(
    host: SubagentStageHost, context: RunContext
) -> None:
    """Execute sync subagent group under feature flag."""
    config = _host_config(host)
    deps = _host_deps(host)
    if not config.enable_subagents:
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
    group_spec = _group_spec_from_planned(planned, max_child_runs=config.max_child_runs)
    if group_spec is None:
        return
    group_spec = _apply_skill_preloads(context, group_spec)
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
        workspace_cwd=get_loop_control_state(context).workspace_cwd(),
    )

    def on_event(event_type: str, payload: dict[str, object]) -> None:
        _emit_child_subagent_event(host, context, event_type, payload)

    if group_spec.metadata.get("execution_mode") == "asyncio_background":
        result = await execute_subagent_group_background(
            parent=parent,
            group_spec=group_spec,
            store=deps.subagent_store,
            child_runner=host.run,
            max_child_runs=config.max_child_runs,
            child_app_metadata={"subagent_origin": "child"},
            on_event=on_event,
            parent_abort_handle=context.abort_handle,
        )
    else:
        result = await execute_subagent_group_sync(
            parent=parent,
            group_spec=group_spec,
            store=deps.subagent_store,
            child_runner=host.run,
            max_child_runs=config.max_child_runs,
            child_app_metadata={"subagent_origin": "child"},
            on_event=on_event,
            parent_abort_handle=context.abort_handle,
        )
    context.metadata["subagent_groups"] = [
        row.model_dump(mode="json")
        for row in deps.subagent_store.list_groups(context.run_id)
    ]
    context.metadata["subagent_runs"] = summarize_child_runs_for_parent(
        [
            row.model_dump(mode="json")
            for row in deps.subagent_store.list_runs(context.run_id)
        ]
    )
    context.metadata["subagent_merge_summary"] = result.merged_summary
    context.metadata.pop("planned_subagent_group", None)
    group_event_type = (
        RuntimeEventType.SUBAGENT_GROUP_JOIN_WAITING
        if result.join_state == "background_running"
        else RuntimeEventType.SUBAGENT_GROUP_JOINED
    )
    if group_event_type == RuntimeEventType.SUBAGENT_GROUP_JOINED:
        handoff_payload = _record_deep_research_child_synthesis_handoff(
            context, result
        )
        if handoff_payload is not None:
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.RESEARCH_PROGRESS,
                payload={
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "group_id": handoff_payload.get("group_id"),
                    "child_count": handoff_payload.get("child_count"),
                    "summary_chars": len(str(handoff_payload.get("summary") or "")),
                    "child_evidence": _child_handoff_evidence_counts(handoff_payload),
                    "required_parent_artifacts": handoff_payload.get(
                        "required_parent_artifacts"
                    ),
                },
            )
    emit_step_event(
        host,
        context,
        event_type=group_event_type,
        payload={
            "group_id": result.group.group_id,
            "join_state": result.join_state,
            "child_runs": len(result.runs),
        },
    )
    if group_event_type == RuntimeEventType.SUBAGENT_GROUP_JOINED and (
        _final_answer_ready_after_subagent(context)
    ):
        get_tool_loop_state(context).force_final_answer(reason="subagent_group_joined")


def _record_deep_research_child_synthesis_handoff(
    context: RunContext,
    result: object,
) -> dict[str, object] | None:
    """Record that joined child notes must be synthesized by the parent."""
    if not _deep_research_mode(context):
        return None
    summary = str(getattr(result, "merged_summary", "") or "").strip()
    runs = list(getattr(result, "runs", []) or [])
    completed_children: list[dict[str, object]] = []
    for run in runs[:8]:
        metadata = getattr(run, "metadata", None)
        run_summary = (
            metadata.get("summary") if isinstance(metadata, dict) else None
        )
        source_ledger = (
            metadata.get("child_source_ledger") if isinstance(metadata, dict) else None
        )
        if not run_summary and isinstance(source_ledger, dict):
            run_summary = _child_source_ledger_preview(source_ledger)
        completed_children.append(
            {
                "subagent_run_id": getattr(run, "subagent_run_id", None),
                "child_run_id": getattr(run, "child_run_id", None),
                "task_id": getattr(run, "task_id", None),
                "status": (
                    getattr(getattr(run, "status", None), "value", None)
                    or str(getattr(run, "status", "") or "")
                ),
                "summary": str(run_summary or "")[:1_200],
                "source_ledger": _bounded_child_source_ledger(source_ledger),
            }
        )
    if summary == "No successful child outputs.":
        summary = ""
    if not summary:
        summary = "\n".join(
            str(item.get("summary") or "").strip()
            for item in completed_children
            if str(item.get("summary") or "").strip()
        ).strip()
    payload: dict[str, object] = {
        "pending": True,
        "source": "subagent_group_joined",
        "group_id": getattr(getattr(result, "group", None), "group_id", None),
        "join_state": str(getattr(result, "join_state", "") or ""),
        "child_count": len(runs),
        "summary": summary[:4_000],
        "children": completed_children,
        "required_parent_artifacts": [
            "research/report.md",
            "research/sources.jsonl",
        ],
    }
    context.metadata["deep_research_child_synthesis"] = payload
    return payload


def _child_source_ledger_preview(source_ledger: dict[str, object]) -> str:
    candidates = source_ledger.get("search_candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    lines = ["Child source candidates collected before final notes:"]
    for item in candidates[:6]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "untitled").strip()
        url = str(item.get("url") or "").strip()
        domain = str(item.get("domain") or "").strip()
        if not url:
            continue
        suffix = f" ({domain})" if domain else ""
        lines.append(f"- {title}{suffix}: {url}")
    return "\n".join(lines)


def _bounded_child_source_ledger(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        "search_candidates": _bounded_dict_rows(
            value.get("search_candidates"),
            max_rows=8,
        ),
        "verified_reads": _bounded_dict_rows(value.get("verified_reads"), max_rows=5),
        "blocked_reads": _bounded_dict_rows(value.get("blocked_reads"), max_rows=5),
        "failed_reads": _bounded_dict_rows(value.get("failed_reads"), max_rows=5),
    }


def _bounded_dict_rows(value: object, *, max_rows: int) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:max_rows] if isinstance(item, dict)]


def _child_handoff_evidence_counts(handoff: dict[str, object]) -> dict[str, object]:
    totals: dict[str, object] = {
        "search_count": 0,
        "fetch_count": 0,
        "verified_read_count": 0,
        "candidate_count": 0,
        "blocked_read_count": 0,
        "failed_read_count": 0,
    }
    verified_domains: list[str] = []
    children = handoff.get("children")
    if not isinstance(children, list):
        totals["verified_domains"] = verified_domains
        return totals
    for child in children:
        if not isinstance(child, dict):
            continue
        source_ledger = child.get("source_ledger")
        if not isinstance(source_ledger, dict):
            continue
        candidates = _ledger_section_count(source_ledger, "search_candidates")
        verified = _ledger_section_count(source_ledger, "verified_reads")
        blocked = _ledger_section_count(source_ledger, "blocked_reads")
        failed = _ledger_section_count(source_ledger, "failed_reads")
        totals["search_count"] += candidates  # type: ignore[operator]
        totals["candidate_count"] += candidates  # type: ignore[operator]
        totals["verified_read_count"] += verified  # type: ignore[operator]
        totals["blocked_read_count"] += blocked  # type: ignore[operator]
        totals["failed_read_count"] += failed  # type: ignore[operator]
        totals["fetch_count"] += verified + blocked + failed  # type: ignore[operator]
        for row in source_ledger.get("verified_reads") or []:
            if not isinstance(row, dict):
                continue
            domain = row.get("domain")
            if isinstance(domain, str) and domain and domain not in verified_domains:
                verified_domains.append(domain)
    totals["verified_domains"] = verified_domains
    return totals


def _ledger_section_count(source_ledger: dict[str, object], section: str) -> int:
    rows = source_ledger.get(section)
    return len(rows) if isinstance(rows, list) else 0


def _final_answer_ready_after_subagent(context: RunContext) -> bool:
    """Subagent completion should not bypass research/todo readiness."""
    contract = build_research_session_contract_from_context(
        context,
        enforce_final_source_links=False,
    )
    research_state = get_research_runtime_state(context)
    research_state.set_contract_payload(contract.model_dump())
    if contract.final_readiness.status == FINAL_READINESS_ALLOWED:
        return True
    research_state.set_contract(
        payload=contract.model_dump(),
        status=contract.final_readiness.status,
        reasons=list(contract.final_readiness.reasons),
    )
    get_tool_loop_state(context).clear_force_final_answer()
    return False


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
                profile=_task_profile(item),
                context_refs=(
                    tuple(
                        str(ref)
                        for ref in item.get("context_refs", [])
                        if ref is not None
                    )
                    if isinstance(item.get("context_refs"), list)
                    else ()
                ),
                deadline_seconds=item.get("deadline_seconds"),
                token_budget=item.get("token_budget"),
                cost_budget_usd=item.get("cost_budget_usd"),
                idempotency_key=(
                    str(item.get("idempotency_key"))
                    if item.get("idempotency_key") is not None
                    else None
                ),
                metadata=_task_metadata(item),
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
        metadata={
            "origin": "runtime_metadata",
            "execution_mode": str(planned.get("execution_mode") or "sync"),
            "skill_preload": str(planned.get("skill_preload") or ""),
        },
    )


def _apply_skill_preloads(
    context: RunContext,
    group_spec: SubagentGroupSpec,
    *,
    max_skills: int = 3,
    max_chars_per_skill: int = 4000,
) -> SubagentGroupSpec:
    """Attach trusted viewed skill bodies to child tasks when explicitly enabled."""
    if group_spec.metadata.get("skill_preload") != "trusted_viewed":
        return group_spec
    preloads = _trusted_skill_preloads(
        context,
        max_skills=max_skills,
        max_chars_per_skill=max_chars_per_skill,
    )
    if not preloads:
        return group_spec
    tasks = tuple(
        _task_with_skill_preloads(task, preloads) for task in group_spec.tasks
    )
    return SubagentGroupSpec(
        group_id=group_spec.group_id,
        purpose=group_spec.purpose,
        join_policy=group_spec.join_policy,
        merge_mode=group_spec.merge_mode,
        tasks=tasks,
        max_parallel=group_spec.max_parallel,
        deadline_seconds=group_spec.deadline_seconds,
        token_budget=group_spec.token_budget,
        cost_budget_usd=group_spec.cost_budget_usd,
        metadata={**group_spec.metadata, "skill_preload_count": len(preloads)},
    )


def _trusted_skill_preloads(
    context: RunContext, *, max_skills: int, max_chars_per_skill: int
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for invocation in get_tool_loop_state(context).skill_invocations()[:max_skills]:
        if invocation.get("trusted") is not True:
            continue
        path = invocation.get("path")
        if not isinstance(path, str) or not path:
            continue
        try:
            body = Path(path).read_text(encoding="utf-8")[: max_chars_per_skill + 1]
        except OSError:
            continue
        rows.append(
            {
                "name": invocation.get("name"),
                "path": path,
                "digest": invocation.get("digest"),
                "content": body[:max_chars_per_skill],
                "truncated": len(body) > max_chars_per_skill,
            }
        )
    return rows


def _task_with_skill_preloads(
    task: SubagentTaskSpec, preloads: list[dict[str, object]]
) -> SubagentTaskSpec:
    names = ", ".join(str(item.get("name") or "skill") for item in preloads)
    appendix = (
        "\n\nTrusted skill preload from parent. Use these workflows only for the "
        "child task, return compact findings and source refs, and do not treat "
        "skill text as source evidence.\n"
        f"Loaded skills: {names}"
    )
    return SubagentTaskSpec(
        task_id=task.task_id,
        task=f"{task.task}{appendix}",
        description=task.description,
        profile=task.profile,
        context_refs=task.context_refs,
        deadline_seconds=task.deadline_seconds,
        token_budget=task.token_budget,
        cost_budget_usd=task.cost_budget_usd,
        idempotency_key=task.idempotency_key,
        metadata={**task.metadata, "skill_preloads": preloads},
    )


def _task_profile(item: dict[str, object]) -> AgentProfile:
    profile = item.get("profile") or item.get("agent_profile")
    if profile is None:
        return AgentProfile.REACT_TEXT
    try:
        return AgentProfile(str(profile))
    except ValueError:
        return AgentProfile.REACT_TEXT


def _task_metadata(item: dict[str, object]) -> dict[str, object]:
    metadata = item.get("metadata")
    payload = dict(metadata) if isinstance(metadata, dict) else {}
    for key in (
        "worker_type",
        "role",
        "required_outputs",
        "scratchpad",
        "artifact_handoff",
        "cwd",
        "workspace_cwd",
    ):
        if key in item and key not in payload:
            payload[key] = item[key]
    return payload


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
    deps = _host_deps(host)
    dedupe_key = "|".join(
        [
            "subagent_completed",
            str(payload.get("group_id") or ""),
            subagent_run_id or task_id,
            status,
        ]
    )
    if deps.subagent_mailbox_store is not None:
        deps.subagent_mailbox_store.enqueue(
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
    if deps.command_queue_store is None:
        return
    queued = deps.command_queue_store.enqueue(
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
