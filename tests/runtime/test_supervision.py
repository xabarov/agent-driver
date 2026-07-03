"""Tests for replayable run supervision state."""

from __future__ import annotations

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.runtime.policy import build_observe_policy_summary
from agent_driver.runtime.supervision import build_run_supervisor_state


def _event(event_type: RuntimeEventType, seq: int, payload: dict[str, object] | None = None) -> dict[str, object]:
    event = new_runtime_event(
        event_type=event_type,
        context={"run_id": "run_supervisor", "attempt_id": "attempt_1", "seq": seq},
        options={"payload": payload or {}},
    )
    return {
        "event": event.type.value,
        "run_id": event.run_id,
        "attempt_id": event.attempt_id,
        "seq": event.seq,
        "data": event.payload,
        "created_at": event.created_at,
    }


def test_supervisor_state_rebuilds_pending_approval_and_reconnect() -> None:
    state = build_run_supervisor_state(
        run_id="run_supervisor",
        events=[
            _event(RuntimeEventType.RUN_STARTED, 1, {"session_id": "session_1"}),
            _event(
                RuntimeEventType.INTERRUPT_REQUESTED,
                2,
                {"interrupt_id": "int_1", "reason": "approval_required"},
            ),
            _event(RuntimeEventType.CHECKPOINT_SAVED, 3),
        ],
        checkpoint_available=True,
    )

    assert state.lifecycle_state == "awaiting_input"
    assert state.heartbeat_status == "active"
    assert state.pending_approvals == [
        {"interrupt_id": "int_1", "reason": "approval_required", "seq": 2}
    ]
    assert state.recoverable is True
    assert state.reconnect_cursor == "run_supervisor:3"


def test_supervisor_state_links_policy_warnings_to_terminal_verdict() -> None:
    events = [
        _event(RuntimeEventType.RUN_COMPLETED, 1),
    ]
    policy = build_observe_policy_summary(
        run_id="run_supervisor",
        events=events,
        task_contract={"required_evidence": ["source_evidence"]},
    )

    state = build_run_supervisor_state(
        run_id="run_supervisor",
        events=events,
        policy_summary=policy,
    )

    assert state.lifecycle_state == "completed"
    assert state.heartbeat_status == "terminal"
    assert state.terminal_verdict == "completed_with_policy_warnings"
    assert state.policy_would_fire_ids == ["required_source_evidence"]
    assert state.last_policy_actions == ["mark_blocked"]


def test_supervisor_state_reports_orphan_truthfully() -> None:
    state = build_run_supervisor_state(
        run_id="run_supervisor",
        events=[_event(RuntimeEventType.RUN_STARTED, 1)],
        active_task=False,
        stale=True,
        orphan_reason="process_restarted_without_task",
    )

    assert state.lifecycle_state == "orphaned"
    assert state.heartbeat_status == "stale"
    assert state.orphaned is True
    assert state.recoverable is False
    assert state.redacted_metadata["process_local_truthfulness"] is True


def test_supervisor_state_tracks_pending_controls_and_retry_counters() -> None:
    state = build_run_supervisor_state(
        run_id="run_supervisor",
        events=[
            _event(
                RuntimeEventType.COMMAND_QUEUED,
                1,
                {"control_id": "ctrl_1", "kind": "enqueue_user_message"},
            ),
            _event(RuntimeEventType.LLM_REQUEST_REJECTED, 2, {"reason": "retry"}),
        ],
    )

    assert state.pending_controls == [
        {
            "control_id": "ctrl_1",
            "kind": "enqueue_user_message",
            "priority": None,
            "seq": 1,
        }
    ]
    assert state.retry_counters == {"retry": 1}
