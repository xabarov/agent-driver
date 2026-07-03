"""Replayable run supervision state derived from existing runtime signals."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.policy import RunSupervisorState
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.policy import build_observe_policy_summary
from agent_driver.runtime.stream import summarize_run_lifecycle

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


def build_run_supervisor_state(
    *,
    events: list[dict[str, object]],
    run_id: str,
    policy_summary: dict[str, Any] | None = None,
    active_task: bool | None = None,
    abort_requested: bool = False,
    abort_reason: str | None = None,
    checkpoint_available: bool = False,
    paused_interrupt_id: str | None = None,
    resume_available: bool | None = None,
    stale: bool = False,
    orphan_reason: str | None = None,
    durability: str = "trace_summary",
    session_id: str | None = None,
    thread_id: str | None = None,
) -> RunSupervisorState:
    """Return truthful single-process supervisor state from event replay.

    This is intentionally a projection, not a scheduler. It only claims what can
    be rebuilt from events plus explicit host metadata.
    """

    stream_events = _stream_events(events, fallback_run_id=run_id)
    lifecycle = summarize_run_lifecycle(
        stream_events,
        run_id=run_id,
        active_task=active_task,
        abort_requested=abort_requested,
        abort_reason=abort_reason,
        checkpoint_available=checkpoint_available,
        paused_interrupt_id=paused_interrupt_id,
        resume_available=resume_available,
        stale=stale,
        orphan_reason=orphan_reason,
        durability=durability,
        session_id=session_id,
        thread_id=thread_id,
    )
    policy = policy_summary or build_observe_policy_summary(
        events=events,
        run_id=run_id,
    )
    policy_profile = policy.get("profile") if isinstance(policy.get("profile"), dict) else {}
    mode = str(policy_profile.get("mode") or "observe")
    would_fire = [
        item for item in policy.get("would_fire_policy_ids", []) if isinstance(item, str)
    ]
    selected_actions = [
        item for item in policy.get("selected_actions", []) if isinstance(item, str)
    ]
    pending_controls = _pending_controls(events)
    pending_approvals = _pending_approvals(
        events,
        paused_interrupt_id=lifecycle.paused_interrupt_id,
    )
    heartbeat_status = _heartbeat_status(
        terminal_event=lifecycle.terminal_event,
        orphaned=lifecycle.orphaned,
        last_seq=lifecycle.last_seq,
    )
    return RunSupervisorState(
        run_id=lifecycle.run_id or run_id,
        session_id=lifecycle.session_id,
        lifecycle_state=str(lifecycle.state.value),
        heartbeat_status=heartbeat_status,
        heartbeat_seq=lifecycle.last_seq,
        current_goal_id=_latest_goal_id(events),
        active_policy_mode=mode,
        pending_controls=pending_controls,
        pending_approvals=pending_approvals,
        retry_counters=_retry_counters(events),
        fallback_counters=_fallback_counters(policy),
        reconnect_cursor=lifecycle.reconnect_cursor,
        terminal_verdict=_terminal_verdict(
            lifecycle_state=str(lifecycle.state.value),
            terminal_reason=lifecycle.terminal_reason,
            would_fire=would_fire,
            selected_actions=selected_actions,
        ),
        recoverable=bool(lifecycle.resume_available or pending_approvals),
        orphaned=lifecycle.orphaned,
        policy_evaluation_count=int(policy.get("count") or 0),
        policy_would_fire_ids=would_fire,
        last_policy_actions=selected_actions,
        redacted_metadata={
            "durability": durability,
            "support_bundle_available": lifecycle.support_bundle_available,
            "process_local_truthfulness": True,
        },
    )


def _stream_events(
    events: list[dict[str, object]],
    *,
    fallback_run_id: str,
) -> list[RunStreamEvent]:
    stream_events: list[RunStreamEvent] = []
    for index, event in enumerate(events, start=1):
        event_name = event.get("event") or event.get("type")
        if not isinstance(event_name, str) or not event_name:
            continue
        data = event.get("data") or event.get("payload")
        run_id = event.get("run_id")
        attempt_id = event.get("attempt_id")
        seq = event.get("seq")
        stream_events.append(
            RunStreamEvent(
                schema_version="1.0",
                stream_id=str(event.get("stream_id") or f"{run_id or fallback_run_id}:{seq or index}"),
                run_id=run_id if isinstance(run_id, str) else fallback_run_id,
                attempt_id=attempt_id if isinstance(attempt_id, str) else "attempt_unknown",
                seq=seq if isinstance(seq, int) and seq > 0 else index,
                event=event_name,
                source="supervisor_replay",
                data=data if isinstance(data, dict) else {},
                created_at=event.get("created_at") if isinstance(event.get("created_at"), str) else None,
            )
        )
    return sorted(stream_events, key=lambda item: item.seq)


def _pending_controls(events: list[dict[str, object]]) -> list[dict[str, Any]]:
    queued: dict[str, dict[str, Any]] = {}
    closed: set[str] = set()
    for index, event in enumerate(events, start=1):
        event_name = event.get("event") or event.get("type")
        data = _event_data(event)
        control_id = _control_id(data, fallback=f"control_{index}")
        if event_name in {"command_queued", "control_requested"}:
            queued[control_id] = {
                "control_id": control_id,
                "kind": data.get("kind"),
                "priority": data.get("priority"),
                "seq": event.get("seq") if isinstance(event.get("seq"), int) else index,
            }
        elif event_name in {"command_dequeued", "command_cancelled", "control_applied"}:
            closed.add(control_id)
    return [item for key, item in queued.items() if key not in closed]


def _pending_approvals(
    events: list[dict[str, object]],
    *,
    paused_interrupt_id: str | None,
) -> list[dict[str, Any]]:
    terminal_seen = any((event.get("event") or event.get("type")) in _TERMINAL_EVENTS for event in events)
    resumed_seen = any((event.get("event") or event.get("type")) == "run_resumed" for event in events)
    if terminal_seen or resumed_seen:
        return []
    approvals: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        event_name = event.get("event") or event.get("type")
        if event_name not in {"interrupt_requested", "run_paused"}:
            continue
        data = _event_data(event)
        interrupt_id = (
            _text(data.get("interrupt_id"))
            or _text(_dict(data.get("interrupt")).get("interrupt_id"))
            or paused_interrupt_id
        )
        if not interrupt_id:
            continue
        approvals.append(
            {
                "interrupt_id": interrupt_id,
                "reason": data.get("reason"),
                "seq": event.get("seq") if isinstance(event.get("seq"), int) else index,
            }
        )
    return approvals[-1:]


def _retry_counters(events: list[dict[str, object]]) -> dict[str, int]:
    counters: dict[str, int] = {}
    for event in events:
        event_name = event.get("event") or event.get("type")
        data = _event_data(event)
        if event_name == "llm_request_rejected":
            key = _text(data.get("reason")) or "llm_request_rejected"
            counters[key] = counters.get(key, 0) + 1
        if event_name == "runtime_decision" and data.get("action") == "retry":
            key = _text(data.get("policy_id")) or _text(data.get("reason")) or "runtime_retry"
            counters[key] = counters.get(key, 0) + 1
    return dict(sorted(counters.items()))


def _fallback_counters(policy_summary: dict[str, Any]) -> dict[str, int]:
    counters: dict[str, int] = {}
    actions = policy_summary.get("selected_actions")
    if not isinstance(actions, list):
        return counters
    for action in actions:
        if action in {"switch_provider_route", "reshape_request"}:
            key = action if isinstance(action, str) else "fallback"
            counters[key] = counters.get(key, 0) + 1
    return counters


def _latest_goal_id(events: list[dict[str, object]]) -> str | None:
    for event in reversed(events):
        data = _event_data(event)
        goal_id = _text(data.get("goal_id"))
        if goal_id:
            return goal_id
        goal = _dict(data.get("goal_state"))
        goal_id = _text(goal.get("goal_id"))
        if goal_id:
            return goal_id
    return None


def _terminal_verdict(
    *,
    lifecycle_state: str,
    terminal_reason: str | None,
    would_fire: list[str],
    selected_actions: list[str],
) -> str | None:
    if lifecycle_state in {"failed", "timed_out", "cancelled"}:
        return terminal_reason or lifecycle_state
    if lifecycle_state == "completed" and any(
        action in {"mark_blocked", "rollback", "fail_fast", "abort"}
        for action in selected_actions
    ):
        return "completed_with_policy_warnings"
    if lifecycle_state == "completed":
        return terminal_reason or "completed"
    if would_fire:
        return "non_terminal_policy_pending"
    return None


def _heartbeat_status(
    *,
    terminal_event: str | None,
    orphaned: bool,
    last_seq: int | None,
) -> str:
    if terminal_event:
        return "terminal"
    if orphaned:
        return "stale"
    if last_seq is not None:
        return "active"
    return "unknown"


def _control_id(data: dict[str, Any], *, fallback: str) -> str:
    return (
        _text(data.get("control_id"))
        or _text(data.get("command_id"))
        or _text(data.get("queue_id"))
        or fallback
    )


def _event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data") or event.get("payload")
    return data if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["build_run_supervisor_state"]
