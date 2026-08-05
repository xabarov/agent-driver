"""Projection helpers from durable runtime events to stream envelopes."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.observability.redaction import redact_sensitive_values as _redact_value
from agent_driver.runtime.storage import RuntimeEventLog

TimelineCategory = Literal[
    "assistant",
    "tool",
    "planning",
    "control",
    "interrupt",
    "usage",
    "artifact",
    "source",
    "compaction",
    "warning",
    "lifecycle",
]

TimelineState = Literal[
    "started",
    "delta",
    "completed",
    "failed",
    "cancelled",
    "paused",
    "recovered",
    "tombstoned",
    "retrying",
]

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}
_CONTROL_EVENTS = {
    "control_requested",
    "control_applied",
    "command_queued",
    "command_dequeued",
    "command_cancelled",
}
_PLANNING_EVENTS = {
    "plan_mode_entered",
    "plan_artifact_updated",
    "plan_approval_requested",
    "plan_approved",
    "plan_rejected",
}
_WARNING_EVENTS = {"warning", "node_contract_warning", "llm_request_rejected"}


class RunLifecycleState(StrEnum):
    """Canonical lifecycle states for reconnect/abort/support diagnostics."""

    QUEUED = "queued"
    RUNNING = "running"
    STREAMING = "streaming"
    PAUSED = "paused"
    AWAITING_INPUT = "awaiting_input"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"


class RunTimelineRow(ContractModel):
    """Domain-neutral row projected from one runtime stream event."""

    row_id: str
    run_id: str
    attempt_id: str
    seq: int
    category: TimelineCategory
    state: TimelineState
    title: str | None = None
    summary: str | None = None
    item_id: str | None = None
    parent_id: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class RuntimeSessionDiagnostics(ContractModel):
    """Compact reconnect/support diagnostics derived from projected rows."""

    harness_id: str | None = None
    adapter_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    stream_contract_version: str = "1.0"
    durability: str = "unknown"
    last_seq: int | None = None
    terminal_state: str | None = None
    terminal_event: str | None = None
    reconnect_cursor: str | None = None
    timeline_row_count: int = 0
    tool_call_count: int = 0
    warning_count: int = 0
    retry_count: int = 0
    usage_present: bool = False
    cost_present: bool = False
    provider_route_profile_id: str | None = None
    sandbox_mode: str | None = None
    skills: list[str] = Field(default_factory=list)
    redaction: dict[str, bool] = Field(
        default_factory=lambda: {"safe_by_default": True}
    )


class RunLifecycleSnapshot(ContractModel):
    """Redaction-safe lifecycle verdict for a run."""

    run_id: str | None = None
    session_id: str | None = None
    thread_id: str | None = None
    state: RunLifecycleState = RunLifecycleState.UNKNOWN
    terminal_event: str | None = None
    terminal_reason: str | None = None
    last_seq: int | None = None
    last_event: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    reconnect_cursor: str | None = None
    active_task: bool | None = None
    abort_requested: bool = False
    abort_reason: str | None = None
    checkpoint_available: bool = False
    paused_interrupt_id: str | None = None
    resume_available: bool = False
    orphaned: bool = False
    orphan_reason: str | None = None
    support_bundle_available: bool = True
    timeline_diagnostics: RuntimeSessionDiagnostics | None = None


def project_runtime_events(events: Iterable[RuntimeEvent]) -> list[RunStreamEvent]:
    """Project runtime event iterable into normalized stream events."""
    return [RunStreamEvent.from_runtime_event(event) for event in events]


def backfill_stream_events(
    event_log: RuntimeEventLog,
    *,
    run_id: str,
    after_seq: int | None = None,
) -> list[RunStreamEvent]:
    """Load persisted runtime events and project to stream envelopes."""
    return project_runtime_events(event_log.list_for_run(run_id, after_seq=after_seq))


def project_run_timeline(events: Iterable[RunStreamEvent]) -> list[RunTimelineRow]:
    """Project stream events into stable, UI-neutral timeline rows."""
    rows: list[RunTimelineRow] = []
    for event in sorted(events, key=lambda item: item.seq):
        rows.extend(_rows_for_event(event))
    return rows


def project_runtime_event_timeline(
    events: Iterable[RuntimeEvent],
) -> list[RunTimelineRow]:
    """Project durable runtime events directly into timeline rows."""
    return project_run_timeline(project_runtime_events(events))


def summarize_runtime_session_diagnostics(
    events: Iterable[RunStreamEvent],
    *,
    durability: str = "unknown",
    harness_id: str | None = None,
    adapter_id: str | None = None,
    session_id: str | None = None,
) -> RuntimeSessionDiagnostics:
    """Return compact runtime/session/timeline diagnostics for support views."""
    ordered = sorted(events, key=lambda item: item.seq)
    rows = project_run_timeline(ordered)
    last = ordered[-1] if ordered else None
    terminal = next((event for event in reversed(ordered) if event.event in _TERMINAL_EVENTS), None)
    first = ordered[0] if ordered else None
    route_profile_id = _latest_route_profile_id(ordered)
    return RuntimeSessionDiagnostics(
        harness_id=harness_id or _first_text(ordered, "harness_id"),
        adapter_id=adapter_id or _first_text(ordered, "adapter_id"),
        session_id=session_id or _first_text(ordered, "session_id"),
        run_id=last.run_id if last else (first.run_id if first else None),
        attempt_id=last.attempt_id if last else (first.attempt_id if first else None),
        stream_contract_version=last.schema_version if last else "1.0",
        durability=durability,
        last_seq=last.seq if last else None,
        terminal_state=_terminal_state(terminal.event) if terminal else None,
        terminal_event=terminal.event if terminal else None,
        reconnect_cursor=f"{last.run_id}:{last.seq}" if last else None,
        timeline_row_count=len(rows),
        tool_call_count=sum(1 for row in rows if row.category == "tool"),
        warning_count=sum(1 for row in rows if row.category == "warning"),
        retry_count=sum(1 for row in rows if row.state == "retrying"),
        usage_present=any(row.category == "usage" for row in rows),
        cost_present=any(bool(row.diagnostics.get("cost_usd")) for row in rows),
        provider_route_profile_id=route_profile_id,
        sandbox_mode=_first_text(ordered, "sandbox_mode"),
        skills=_first_text_list(ordered, "skills"),
    )


def classify_run_lifecycle_from_events(
    events: Iterable[RunStreamEvent],
    *,
    active_task: bool | None = None,
    abort_requested: bool = False,
    abort_reason: str | None = None,
    checkpoint_available: bool = False,
    paused_interrupt_id: str | None = None,
    resume_available: bool | None = None,
    stale: bool = False,
    orphan_reason: str | None = None,
) -> RunLifecycleState:
    """Classify a run lifecycle from stream events plus optional live metadata."""
    ordered = sorted(events, key=lambda item: item.seq)
    if not ordered:
        if stale or (active_task is False and orphan_reason):
            return RunLifecycleState.ORPHANED
        if abort_requested:
            return RunLifecycleState.CANCELLING
        if paused_interrupt_id:
            return (
                RunLifecycleState.AWAITING_INPUT
                if bool(resume_available if resume_available is not None else checkpoint_available)
                else RunLifecycleState.PAUSED
            )
        if active_task is True:
            return RunLifecycleState.RUNNING
        return RunLifecycleState.UNKNOWN
    terminal = next((event for event in reversed(ordered) if event.event in _TERMINAL_EVENTS), None)
    if terminal is not None:
        if terminal.event == "run_cancelled":
            return RunLifecycleState.CANCELLED
        if terminal.event == "run_failed":
            return (
                RunLifecycleState.TIMED_OUT
                if _terminal_timed_out(terminal)
                else RunLifecycleState.FAILED
            )
        return RunLifecycleState.COMPLETED
    if stale or (active_task is False and orphan_reason):
        return RunLifecycleState.ORPHANED
    if abort_requested:
        return RunLifecycleState.CANCELLING
    if paused_interrupt_id or _last_interrupt_id(ordered):
        return (
            RunLifecycleState.AWAITING_INPUT
            if bool(resume_available if resume_available is not None else checkpoint_available)
            else RunLifecycleState.PAUSED
        )
    last_event = ordered[-1].event
    if last_event == "run_queued":
        return RunLifecycleState.QUEUED
    if any(event.event in {"token_delta", "reasoning_delta"} for event in ordered):
        return RunLifecycleState.STREAMING
    return RunLifecycleState.RUNNING


def summarize_run_lifecycle(
    events: Iterable[RunStreamEvent],
    *,
    run_id: str | None = None,
    active_task: bool | None = None,
    abort_requested: bool = False,
    abort_reason: str | None = None,
    checkpoint_available: bool = False,
    paused_interrupt_id: str | None = None,
    resume_available: bool | None = None,
    stale: bool = False,
    orphan_reason: str | None = None,
    durability: str = "unknown",
    harness_id: str | None = None,
    adapter_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
) -> RunLifecycleSnapshot:
    """Return a canonical lifecycle snapshot for support/reconnect surfaces."""
    ordered = sorted(events, key=lambda item: item.seq)
    diagnostics = summarize_runtime_session_diagnostics(
        ordered,
        durability=durability,
        harness_id=harness_id,
        adapter_id=adapter_id,
        session_id=session_id,
    )
    first = ordered[0] if ordered else None
    last = ordered[-1] if ordered else None
    terminal = next((event for event in reversed(ordered) if event.event in _TERMINAL_EVENTS), None)
    interrupt_id = paused_interrupt_id or _last_interrupt_id(ordered)
    resume = bool(resume_available if resume_available is not None else (checkpoint_available and interrupt_id))
    state = classify_run_lifecycle_from_events(
        ordered,
        active_task=active_task,
        abort_requested=abort_requested,
        abort_reason=abort_reason,
        checkpoint_available=checkpoint_available,
        paused_interrupt_id=interrupt_id,
        resume_available=resume,
        stale=stale,
        orphan_reason=orphan_reason,
    )
    orphaned = state == RunLifecycleState.ORPHANED
    return RunLifecycleSnapshot(
        run_id=run_id or diagnostics.run_id,
        session_id=diagnostics.session_id,
        thread_id=thread_id or diagnostics.session_id,
        state=state,
        terminal_event=terminal.event if terminal else None,
        terminal_reason=_terminal_reason(terminal) if terminal else None,
        last_seq=last.seq if last else None,
        last_event=last.event if last else None,
        started_at=first.created_at if first else None,
        updated_at=last.created_at if last else None,
        completed_at=terminal.created_at if terminal else None,
        reconnect_cursor=diagnostics.reconnect_cursor,
        active_task=active_task,
        abort_requested=abort_requested,
        abort_reason=abort_reason,
        checkpoint_available=checkpoint_available,
        paused_interrupt_id=interrupt_id,
        resume_available=resume,
        orphaned=orphaned,
        orphan_reason=orphan_reason if orphaned else None,
        support_bundle_available=True,
        timeline_diagnostics=diagnostics,
    )


def _rows_for_event(event: RunStreamEvent) -> list[RunTimelineRow]:
    data = event.data
    if event.event == "tool_call_started":
        return [_tool_row(event, tool, "started") for tool in _event_tools(data)]
    if event.event == "tool_progress":
        return [_tool_row(event, tool, "delta") for tool in _event_tools(data)]
    if event.event == "tool_call_completed":
        return [_tool_row(event, tool, _tool_state(tool)) for tool in _event_tools(data)]
    if event.event == "llm_call_completed" and _has_usage(data):
        return [_base_row(event, category="usage", state="completed")]
    category, state = _category_state(event.event)
    return [_base_row(event, category=category, state=state)]


def _base_row(
    event: RunStreamEvent,
    *,
    category: TimelineCategory,
    state: TimelineState,
    item_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> RunTimelineRow:
    item = item_id or _item_id(event.data) or event.event
    diagnostics = _diagnostics_for_event(event)
    return RunTimelineRow(
        row_id=_row_id(event.run_id, event.seq, category, item),
        run_id=event.run_id,
        attempt_id=event.attempt_id,
        seq=event.seq,
        category=category,
        state=state,
        title=title or _title_for_event(event),
        summary=summary or _summary_for_event(event),
        item_id=item,
        parent_id=_text(event.data.get("parent_id")),
        app_metadata=_redact_value(_dict_value(event.data.get("app_metadata"))),
        diagnostics=diagnostics,
        created_at=event.created_at,
    )


def _tool_row(
    event: RunStreamEvent,
    tool: dict[str, Any],
    state: TimelineState,
) -> RunTimelineRow:
    name = _text(tool.get("tool_name")) or _text(tool.get("name")) or "tool"
    call_id = (
        _text(tool.get("tool_call_id"))
        or _text(tool.get("call_id"))
        or _text(tool.get("id"))
        or name
    )
    summary = (
        _text(tool.get("result_summary"))
        or _text(tool.get("status"))
        or _text(tool.get("risk"))
    )
    return _base_row(
        event,
        category="tool",
        state=state,
        item_id=call_id,
        title=name,
        summary=summary,
    )


def _category_state(event_name: str) -> tuple[TimelineCategory, TimelineState]:
    if event_name in {"assistant_message_started"}:
        return "assistant", "started"
    if event_name in {"token_delta", "reasoning_delta"}:
        return "assistant", "delta"
    if event_name in {"assistant_message_completed"}:
        return "assistant", "completed"
    if event_name in {"assistant_message_replaced", "run_resumed"}:
        return "assistant", "recovered"
    if event_name in {"assistant_message_tombstoned"}:
        return "assistant", "tombstoned"
    if event_name in _PLANNING_EVENTS:
        return "planning", _planning_state(event_name)
    if event_name in _CONTROL_EVENTS:
        return "control", _control_state(event_name)
    if event_name in {"interrupt_requested", "run_paused"}:
        return "interrupt", "paused"
    if event_name in {"artifact_created", "artifact_updated"}:
        return "artifact", "completed" if event_name.endswith("created") else "delta"
    if event_name in {
        "source_ledger_updated",
        "citation_coverage_updated",
        "research_progress",
    }:
        return "source", "completed" if event_name == "source_ledger_updated" else "delta"
    if event_name == "memory_compaction_started":
        return "compaction", "started"
    if event_name == "memory_compacted":
        return "compaction", "completed"
    if event_name in _WARNING_EVENTS:
        return "warning", "retrying" if event_name == "llm_request_rejected" else "completed"
    if event_name == "run_failed":
        return "lifecycle", "failed"
    if event_name == "run_cancelled":
        return "lifecycle", "cancelled"
    if event_name == "run_completed":
        return "lifecycle", "completed"
    if event_name in {"run_started", "run_queued", "node_started", "llm_call_started"}:
        return "lifecycle", "started"
    if event_name in {"node_completed", "checkpoint_saved"}:
        return "lifecycle", "completed"
    return "lifecycle", "delta"


def _planning_state(event_name: str) -> TimelineState:
    if event_name in {"plan_rejected"}:
        return "failed"
    if event_name in {"plan_approved"}:
        return "completed"
    return "started" if event_name == "plan_mode_entered" else "delta"


def _control_state(event_name: str) -> TimelineState:
    if event_name == "command_cancelled":
        return "cancelled"
    if event_name in {"control_applied", "command_dequeued"}:
        return "completed"
    return "started"


def _tool_state(tool: dict[str, Any]) -> TimelineState:
    status = _text(tool.get("status"))
    if status in {"failed", "error", "timed_out", "timeout", "denied"}:
        return "failed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    return "completed"


def _terminal_state(event_name: str) -> str:
    if event_name == "run_failed":
        return "failed"
    if event_name == "run_cancelled":
        return "cancelled"
    return "completed"


def _terminal_reason(event: RunStreamEvent | None) -> str | None:
    if event is None:
        return None
    for key in ("terminal_reason", "reason", "status", "error"):
        value = _text(event.data.get(key))
        if value:
            return value
    return None


def _terminal_timed_out(event: RunStreamEvent) -> bool:
    reason = (_terminal_reason(event) or "").lower()
    return reason in {"timed_out", "timeout"} or "timeout" in reason


def _last_interrupt_id(events: list[RunStreamEvent]) -> str | None:
    for event in reversed(events):
        if event.event not in {"interrupt_requested", "run_paused"}:
            continue
        value = (
            _text(event.data.get("interrupt_id"))
            or _text(_dict_value(event.data.get("interrupt")).get("interrupt_id"))
        )
        if value:
            return value
    return None


def _event_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
    tools = data.get("tools")
    if isinstance(tools, list):
        return [dict(item) for item in tools if isinstance(item, dict)]
    if isinstance(data.get("tool_name"), str):
        return [dict(data)]
    return [dict(data)]


def _has_usage(data: dict[str, Any]) -> bool:
    usage = data.get("usage")
    if isinstance(usage, dict) and usage:
        return True
    return any(
        key in data
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_usd",
            "cost",
        )
    )


def _diagnostics_for_event(event: RunStreamEvent) -> dict[str, Any]:
    data = event.data
    usage = _dict_value(data.get("usage"))
    cost = data.get("cost_usd", data.get("cost"))
    route = _dict_value(data.get("route_profile"))
    preflight = _dict_value(data.get("provider_preflight"))
    diagnostics: dict[str, Any] = {
        "event": event.event,
        "stream_id": event.stream_id,
    }
    if usage:
        diagnostics["usage"] = usage
    if isinstance(cost, int | float):
        diagnostics["cost_usd"] = cost
    profile_id = _text(route.get("profile_id")) or _text(preflight.get("route_profile_id"))
    if profile_id:
        diagnostics["provider_route_profile_id"] = profile_id
    return diagnostics


def _title_for_event(event: RunStreamEvent) -> str:
    data = event.data
    for key in ("title", "tool_name", "status", "reason", "phase"):
        value = _text(data.get(key))
        if value:
            return value
    return event.event.replace("_", " ")


def _summary_for_event(event: RunStreamEvent) -> str | None:
    data = event.data
    for key in ("summary", "result_summary", "delta_text", "content", "reason"):
        value = _text(data.get(key))
        if value:
            return value[:240]
    return None


def _item_id(data: dict[str, Any]) -> str | None:
    for key in (
        "tool_call_id",
        "call_id",
        "artifact_id",
        "path",
        "queue_id",
        "control_id",
        "compaction_id",
        "message_id",
    ):
        value = _text(data.get(key))
        if value:
            return value
    return None


def _row_id(run_id: str, seq: int, category: str, item_id: str | None) -> str:
    suffix = (item_id or "event").replace(" ", "_").replace("/", "__")
    return f"{run_id}:{seq}:{category}:{suffix}"


def _latest_route_profile_id(events: list[RunStreamEvent]) -> str | None:
    for event in reversed(events):
        diagnostics = _diagnostics_for_event(event)
        profile_id = _text(diagnostics.get("provider_route_profile_id"))
        if profile_id:
            return profile_id
    return None


def _first_text(events: list[RunStreamEvent], key: str) -> str | None:
    for event in events:
        value = _text(event.data.get(key))
        if value:
            return value
    return None


def _first_text_list(events: list[RunStreamEvent], key: str) -> list[str]:
    for event in events:
        value = event.data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
    return []


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None




__all__ = [
    "RuntimeSessionDiagnostics",
    "RunLifecycleSnapshot",
    "RunLifecycleState",
    "RunTimelineRow",
    "backfill_stream_events",
    "classify_run_lifecycle_from_events",
    "project_run_timeline",
    "project_runtime_event_timeline",
    "project_runtime_events",
    "summarize_run_lifecycle",
    "summarize_runtime_session_diagnostics",
]
