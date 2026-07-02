"""Tests for runtime stream projection/backfill helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore
from agent_driver.runtime.stream import (
    backfill_stream_events,
    project_run_timeline,
    project_runtime_event_timeline,
    project_runtime_events,
    summarize_runtime_session_diagnostics,
)


def test_project_runtime_events_maps_type_and_seq() -> None:
    """Projection should preserve ordering and map event type."""
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_a", "attempt_id": "att_1", "seq": 1},
        ),
        new_runtime_event(
            event_type=RuntimeEventType.LLM_CALL_COMPLETED,
            context={"run_id": "run_a", "attempt_id": "att_1", "seq": 2},
            options={"payload": {"model": "fake"}},
        ),
    ]
    projected = project_runtime_events(events)
    assert [item.seq for item in projected] == [1, 2]
    assert projected[1].event == "llm_call_completed"
    assert projected[1].data["model"] == "fake"


@pytest.mark.parametrize(
    "event_type",
    [
        RuntimeEventType.RUN_STARTED,
        RuntimeEventType.RUN_RESUMED,
        RuntimeEventType.RUN_PAUSED,
        RuntimeEventType.RUN_COMPLETED,
        RuntimeEventType.LLM_CALL_STARTED,
        RuntimeEventType.TOKEN_DELTA,
        RuntimeEventType.LLM_CALL_COMPLETED,
        RuntimeEventType.TOOL_CALL_STARTED,
        RuntimeEventType.TOOL_CALL_COMPLETED,
        RuntimeEventType.INTERRUPT_REQUESTED,
        RuntimeEventType.CONTROL_REQUESTED,
        RuntimeEventType.COMMAND_QUEUED,
        RuntimeEventType.COMMAND_DEQUEUED,
        RuntimeEventType.COMMAND_CANCELLED,
        RuntimeEventType.CONTROL_APPLIED,
        RuntimeEventType.PLAN_MODE_ENTERED,
        RuntimeEventType.PLAN_ARTIFACT_UPDATED,
        RuntimeEventType.PLAN_APPROVAL_REQUESTED,
        RuntimeEventType.PLAN_APPROVED,
        RuntimeEventType.PLAN_REJECTED,
        RuntimeEventType.WARNING,
        RuntimeEventType.RUN_FAILED,
    ],
)
def test_project_runtime_events_covers_lifecycle_categories(
    event_type: RuntimeEventType,
) -> None:
    """Projection should preserve lifecycle event type names across categories."""
    projected = project_runtime_events(
        [
            new_runtime_event(
                event_type=event_type,
                context={"run_id": "run_lifecycle", "attempt_id": "att_1", "seq": 1},
            )
        ]
    )
    assert projected[0].event == event_type.value


def test_backfill_stream_events_respects_after_seq() -> None:
    """Backfill helper should honor after_seq filter."""
    log = InMemoryEventLog()
    log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_b", "attempt_id": "att_1", "seq": 1},
        )
    )
    log.append(
        new_runtime_event(
            event_type=RuntimeEventType.LLM_CALL_STARTED,
            context={"run_id": "run_b", "attempt_id": "att_1", "seq": 2},
        )
    )
    rows = backfill_stream_events(log, run_id="run_b", after_seq=1)
    assert len(rows) == 1
    assert rows[0].seq == 2


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_backfill_stream_events_supported_for_memory_and_sqlite(
    tmp_path: Path, backend: str
) -> None:
    """Backfill should work with both in-memory and sqlite runtime logs."""
    if backend == "sqlite":
        event_log = SqliteRuntimeStore(path=str(tmp_path / "stream_backfill.sqlite3"))
    else:
        event_log = InMemoryEventLog()
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_store", "attempt_id": "att_1", "seq": 1},
        )
    )
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.TOKEN_DELTA,
            context={"run_id": "run_store", "attempt_id": "att_1", "seq": 2},
            options={"payload": {"delta_text": "hello"}},
        )
    )
    rows = backfill_stream_events(event_log, run_id="run_store", after_seq=1)
    assert len(rows) == 1
    assert rows[0].event == RuntimeEventType.TOKEN_DELTA.value
    assert rows[0].stream_id == "run_store:2"


def test_project_run_timeline_covers_core_row_types() -> None:
    """Timeline projection should expose stable domain-neutral rows."""
    events = project_runtime_events(
        [
            new_runtime_event(
                event_type=RuntimeEventType.ASSISTANT_MESSAGE_STARTED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 1},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.TOKEN_DELTA,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 2},
                options={"payload": {"delta_text": "hello"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 3},
                options={"payload": {"content": "hello world"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.TOOL_CALL_STARTED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 4},
                options={
                    "payload": {
                        "tool_name": "web_search",
                        "tool_call_id": "tc1",
                        "args": {"query": "secret-free"},
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 5},
                options={
                    "payload": {
                        "tool_name": "web_search",
                        "tool_call_id": "tc1",
                        "status": "completed",
                        "result_summary": "found sources",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.LLM_CALL_COMPLETED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 6},
                options={
                    "payload": {
                        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                        "cost_usd": 0.01,
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_timeline", "attempt_id": "att_1", "seq": 7},
            ),
        ]
    )

    rows = project_run_timeline(events)

    assert [(row.category, row.state) for row in rows] == [
        ("assistant", "started"),
        ("assistant", "delta"),
        ("assistant", "completed"),
        ("tool", "started"),
        ("tool", "completed"),
        ("usage", "completed"),
        ("lifecycle", "completed"),
    ]
    assert rows[1].summary == "hello"
    assert rows[3].row_id == "run_timeline:4:tool:tc1"
    assert rows[4].title == "web_search"
    assert rows[5].diagnostics["usage"]["prompt_tokens"] == 10
    assert rows[5].diagnostics["cost_usd"] == 0.01


def test_project_run_timeline_covers_warning_retry_recovery_and_redaction() -> None:
    """Timeline should keep warning/retry/recovery states and redact metadata."""
    rows = project_runtime_event_timeline(
        [
            new_runtime_event(
                event_type=RuntimeEventType.LLM_REQUEST_REJECTED,
                context={"run_id": "run_retry", "attempt_id": "att_1", "seq": 1},
                options={"payload": {"reason": "forced tool_choice rejected"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_RESUMED,
                context={"run_id": "run_retry", "attempt_id": "att_1", "seq": 2},
                options={
                    "payload": {
                        "app_metadata": {
                            "safe": "ok",
                            "api_key": "secret",
                            "base_url": "https://example.test",
                        }
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.WARNING,
                context={"run_id": "run_retry", "attempt_id": "att_1", "seq": 3},
                options={"payload": {"summary": "recovered with auto tool choice"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_FAILED,
                context={"run_id": "run_retry", "attempt_id": "att_1", "seq": 4},
            ),
        ]
    )

    assert [(row.category, row.state) for row in rows] == [
        ("warning", "retrying"),
        ("assistant", "recovered"),
        ("warning", "completed"),
        ("lifecycle", "failed"),
    ]
    assert rows[1].app_metadata["safe"] == "ok"
    assert rows[1].app_metadata["api_key"] == "<redacted>"
    assert rows[1].app_metadata["base_url"] == "<redacted>"


def test_runtime_session_diagnostics_reports_terminal_cursor_and_counts() -> None:
    """Diagnostics should summarize reconnect, terminal state and row counts."""
    events = project_runtime_events(
        [
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={"run_id": "run_diag", "attempt_id": "att_1", "seq": 1},
                options={
                    "payload": {
                        "harness_id": "chat-demo",
                        "adapter_id": "sse",
                        "session_id": "session_1",
                        "sandbox_mode": "workspace-write",
                        "skills": ["deep-research-report"],
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
                context={"run_id": "run_diag", "attempt_id": "att_1", "seq": 2},
                options={"payload": {"tool_name": "web_fetch"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.LLM_REQUEST_REJECTED,
                context={"run_id": "run_diag", "attempt_id": "att_1", "seq": 3},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.LLM_CALL_COMPLETED,
                context={"run_id": "run_diag", "attempt_id": "att_1", "seq": 4},
                options={
                    "payload": {
                        "usage": {"total_tokens": 42},
                        "route_profile": {"profile_id": "openrouter:route"},
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_CANCELLED,
                context={"run_id": "run_diag", "attempt_id": "att_1", "seq": 5},
            ),
        ]
    )

    diagnostics = summarize_runtime_session_diagnostics(events, durability="sqlite")

    assert diagnostics.harness_id == "chat-demo"
    assert diagnostics.session_id == "session_1"
    assert diagnostics.last_seq == 5
    assert diagnostics.terminal_state == "cancelled"
    assert diagnostics.terminal_event == "run_cancelled"
    assert diagnostics.reconnect_cursor == "run_diag:5"
    assert diagnostics.timeline_row_count == 5
    assert diagnostics.tool_call_count == 1
    assert diagnostics.warning_count == 1
    assert diagnostics.retry_count == 1
    assert diagnostics.usage_present is True
    assert diagnostics.provider_route_profile_id == "openrouter:route"
    assert diagnostics.skills == ["deep-research-report"]
