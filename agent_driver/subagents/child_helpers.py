"""Child-run helper layer for subagent execution (input build + budgets + result build).

Extracted from ``subagents/executor`` (god-module split, behaviour-neutral). A leaf layer:
pure builders that shape a child ``AgentRunInput`` and turn a child ``AgentRunOutput`` into a
``SubagentRun`` — no coupling to the async execution spine or the ``SubagentExecutionResult``
class. Re-exported from ``executor`` for the spine and for existing callers/tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from inspect import signature
from typing import Any, Callable
from uuid import uuid4

from agent_driver.contracts.artifacts import ArtifactRef
from agent_driver.contracts.enums import (
    ParentStateWriteMode,
    SubagentExecutionMode,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.subagents import MergeProvenance, SubagentGroup, SubagentRun
from agent_driver.subagents.handoff import SubagentParentHandoff
from agent_driver.subagents.isolation import ChildWorkspace
from agent_driver.subagents.planner import build_child_context_handoff
from agent_driver.subagents.specs import SubagentTaskSpec
from agent_driver.subagents.workers import apply_worker_tool_surface

ChildRunner = Callable[[AgentRunInput], "object"]


_MAX_CHILD_OUTPUT_ARTIFACT_REFS = 8


_DEFAULT_CHILD_DEADLINE_SECONDS = 90.0


_DEFAULT_CHILD_MAX_STEPS = 8


_DEFAULT_CHILD_MAX_TOOL_CALLS = 6


_DEFAULT_DEEP_RESEARCH_CHILD_MAX_STEPS = 10


_DEFAULT_DEEP_RESEARCH_CHILD_MAX_TOOL_CALLS = 6


_BUDGET_TERMINAL_REASONS = frozenset(
    {"max_steps_exceeded", "tool_policy_denied", "deadline_exceeded", "budget_exceeded"}
)


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
    policy = apply_worker_tool_surface(
        parent_tool_policy=parent.tool_policy,
        worker_type=str(worker_type) if worker_type is not None else None,
    )
    policy = _apply_task_tool_surface(
        policy=policy,
        parent=parent,
        task=task,
        worker_type=str(worker_type) if worker_type is not None else None,
    )
    if task.metadata.get("deep_research_child_notes_only") is True:
        policy = _strip_parent_research_contract(policy)
    return policy


def _apply_task_tool_surface(
    *,
    policy: dict[str, object],
    parent: SubagentParentHandoff,
    task: SubagentTaskSpec,
    worker_type: str | None,
) -> dict[str, object]:
    """Narrow a child policy by host-declared and task-declared surfaces.

    A model-authored ``agent_tool`` request may select a role and ask for a
    smaller set of tools, but it must never widen the parent's allow-list.
    Hosts can make that narrowing mandatory by declaring
    ``task_contract.child_tool_surfaces`` and ``child_denied_tools`` in the
    parent policy metadata.  This keeps product-specific role names and tool
    names outside agent-driver while making leaf-agent least privilege
    enforceable rather than prompt-only.
    """

    parent_metadata = parent.tool_policy.get("metadata")
    task_contract = (
        parent_metadata.get("task_contract")
        if isinstance(parent_metadata, dict)
        else None
    )
    contract_allowed: object = None
    contract_denied: object = None
    if isinstance(task_contract, dict):
        surfaces = task_contract.get("child_tool_surfaces")
        if isinstance(surfaces, dict):
            # Once a host declares an exhaustive child surface map, an
            # unknown/missing model-selected role is denied by default.  It
            # must never fall back to the broader parent surface.
            contract_allowed = surfaces.get(worker_type, []) if worker_type else []
        contract_denied = task_contract.get("child_denied_tools")

    requested_surfaces = [
        value
        for value in (contract_allowed, task.metadata.get("allowed_tools"))
        if isinstance(value, list)
    ]
    current_allowed = policy.get("allowed_tools")
    allowed = (
        [str(value) for value in current_allowed]
        if isinstance(current_allowed, list)
        else None
    )
    for requested in requested_surfaces:
        requested_set = {str(value) for value in requested}
        if allowed is None:
            allowed = [str(value) for value in requested]
        else:
            allowed = [value for value in allowed if value in requested_set]

    denied: list[str] = []
    for source in (
        policy.get("denied_tools"),
        contract_denied,
        task.metadata.get("denied_tools"),
    ):
        if not isinstance(source, list):
            continue
        for value in source:
            normalized = str(value)
            if normalized not in denied:
                denied.append(normalized)
    if allowed is not None and denied:
        denied_set = set(denied)
        allowed = [value for value in allowed if value not in denied_set]

    if not requested_surfaces and not denied:
        return policy
    metadata = dict(policy.get("metadata") or {})
    metadata.update(
        {
            "task_tool_surface": "narrowed",
            "task_worker_type": worker_type,
            "task_requested_allowed_tools": [
                [str(value) for value in source] for source in requested_surfaces
            ],
            "task_denied_tools": denied,
        }
    )
    result = {**policy, "metadata": metadata}
    if allowed is not None:
        result["allowed_tools"] = allowed
    if denied:
        result["denied_tools"] = denied
    return result


def _strip_parent_research_contract(policy: dict[str, object]) -> dict[str, object]:
    metadata = dict(policy.get("metadata") or {})
    metadata.pop("deep_research_mode", None)
    metadata.pop("deep_research_phase_gate", None)
    task_contract = metadata.get("task_contract")
    parent_profile = ""
    if isinstance(task_contract, dict):
        parent_profile = (
            str(task_contract.get("research_profile") or "").strip().lower()
        )
        metadata["parent_task_contract"] = {
            "research_profile": task_contract.get("research_profile"),
            "research_mode": task_contract.get("research_mode"),
            "research_depth": task_contract.get("research_depth"),
        }
    metadata.pop("task_contract", None)
    metadata["child_contract"] = "deep_research_source_notes"
    # Deep Research children are leaf researchers. Stripping the parent's deep
    # (delegating) contract is intentional — a child must not re-delegate — but a
    # child with NO research contract has no fetch-discipline gate, so it can run
    # many web_search calls and never open a page. Give medium/hard children
    # their own source-verified contract (research_mode="web", so it stays a
    # leaf) to enforce the same search -> fetch escalation the parent gets.
    if parent_profile in {"medium", "hard"} and _child_research_can_fetch(policy):
        metadata["task_contract"] = {
            "kind": "research",
            "requires_research": True,
            "research_mode": "web",
            "research_depth": "source_verified_report",
            "fetch_required": True,
            "research_profile": parent_profile,
        }
    return {**policy, "metadata": metadata}


def _child_research_can_fetch(policy: Mapping[str, Any]) -> bool:
    """Return True when the child tool surface exposes a page-reading tool."""
    allowed = policy.get("allowed_tools")
    if not isinstance(allowed, list):
        return True
    fetch_tools = {"web_fetch", "source_read", "pdf_read", "browser_read"}
    return any(str(tool) in fetch_tools for tool in allowed)


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
        deadline_seconds=_child_deadline_seconds(task),
        max_steps=_child_max_steps(task),
        max_tool_calls=_child_max_tool_calls(task),
        app_metadata=_child_app_metadata(
            parent=parent,
            group=group,
            child_app_metadata=child_app_metadata,
            workspace=workspace,
        ),
    )


def _child_budget_summary(
    task: SubagentTaskSpec, output: AgentRunOutput
) -> dict[str, object]:
    """Structured budget/exhaustion marker the parent can reason about."""
    reason = output.terminal_reason.value if output.terminal_reason else None
    return {
        "budget_exhausted": reason in _BUDGET_TERMINAL_REASONS,
        "terminal_reason": reason,
        "max_steps": _child_max_steps(task),
        "max_tool_calls": _child_max_tool_calls(task),
        "deadline_seconds": _child_deadline_seconds(task),
    }


def _child_deadline_seconds(task: SubagentTaskSpec) -> float | None:
    if task.deadline_seconds is not None:
        return task.deadline_seconds
    return _DEFAULT_CHILD_DEADLINE_SECONDS


def _child_max_steps(task: SubagentTaskSpec) -> int:
    raw = task.metadata.get("max_steps")
    if isinstance(raw, int) and raw > 0:
        return raw
    if task.metadata.get("deep_research_child_notes_only") is True:
        return _DEFAULT_DEEP_RESEARCH_CHILD_MAX_STEPS
    return _DEFAULT_CHILD_MAX_STEPS


def _child_max_tool_calls(task: SubagentTaskSpec) -> int:
    raw = task.metadata.get("max_tool_calls")
    if isinstance(raw, int) and raw > 0:
        return raw
    if task.metadata.get("deep_research_child_notes_only") is True:
        return _DEFAULT_DEEP_RESEARCH_CHILD_MAX_TOOL_CALLS
    return _DEFAULT_CHILD_MAX_TOOL_CALLS


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
            "child_source_ledger": _child_source_ledger_from_output(output),
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
            "child_budget": _child_budget_summary(task, output),
        },
    )


def _child_source_ledger_from_output(
    output: AgentRunOutput,
    *,
    max_candidates: int = 12,
    max_verified_reads: int = 6,
    max_failed_reads: int = 6,
    max_blocked_reads: int = 6,
) -> dict[str, object]:
    ledger: dict[str, object] = {}
    for event in output.events:
        event_type = getattr(event, "type", None)
        event_name = getattr(event_type, "value", None) or str(event_type or "")
        if event_name != "source_ledger_updated":
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        ledger = {
            "search_candidates": _bounded_dict_rows(
                payload.get("search_candidates"),
                max_rows=max_candidates,
            ),
            "verified_reads": _bounded_dict_rows(
                payload.get("verified_reads"),
                max_rows=max_verified_reads,
            ),
            "failed_reads": _bounded_dict_rows(
                payload.get("failed_reads"),
                max_rows=max_failed_reads,
            ),
            "blocked_reads": _bounded_dict_rows(
                payload.get("blocked_reads"),
                max_rows=max_blocked_reads,
            ),
        }
    return ledger


def _bounded_dict_rows(value: object, *, max_rows: int) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, object]] = []
    for item in value[:max_rows]:
        if isinstance(item, dict):
            rows.append(dict(item))
    return rows


def _child_evidence_summary(metadata: dict[str, object]) -> dict[str, int]:
    ledger = metadata.get("child_source_ledger")
    if not isinstance(ledger, dict):
        return {
            "search_count": 0,
            "fetch_count": 0,
            "verified_read_count": 0,
            "candidate_count": 0,
            "blocked_read_count": 0,
            "failed_read_count": 0,
        }
    verified_reads = _ledger_rows(ledger.get("verified_reads"))
    candidates = _ledger_rows(ledger.get("search_candidates"))
    blocked_reads = _ledger_rows(ledger.get("blocked_reads"))
    failed_reads = _ledger_rows(ledger.get("failed_reads"))
    return {
        "search_count": len(candidates),
        "fetch_count": len(verified_reads) + len(blocked_reads) + len(failed_reads),
        "verified_read_count": len(verified_reads),
        "candidate_count": len(candidates),
        "blocked_read_count": len(blocked_reads),
        "failed_read_count": len(failed_reads),
    }


def _ledger_rows(value: object) -> list[object]:
    return value if isinstance(value, list) else []


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


__all__ = [
    "ChildRunner",
    "_status_from_output",
    "_call_child_runner",
    "_bounded_output_artifact_refs",
    "_first_output_artifact",
    "_child_artifact_audit",
    "_carried_child_keys",
    "_child_tool_policy",
    "_strip_parent_research_contract",
    "_child_research_can_fetch",
    "_child_app_metadata",
    "_child_abort_handle",
    "_build_pending_child_run",
    "_build_child_input",
    "_child_budget_summary",
    "_child_deadline_seconds",
    "_child_max_steps",
    "_child_max_tool_calls",
    "_cancelled_child_run",
    "_completed_child_run_from_output",
    "_child_source_ledger_from_output",
    "_bounded_dict_rows",
    "_child_evidence_summary",
    "_ledger_rows",
    "_failed_child_run",
]
