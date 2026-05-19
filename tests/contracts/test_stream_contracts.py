"""Tests for transport-neutral stream contracts."""

from __future__ import annotations

from agent_driver.contracts import RuntimeEventType, RunStreamEvent, new_runtime_event


def test_run_stream_event_projection_keeps_ids_and_payload() -> None:
    """Runtime event projection should keep run/seq/payload fields."""
    runtime_event = new_runtime_event(
        event_type=RuntimeEventType.LLM_CALL_STARTED,
        context={"run_id": "run_1", "attempt_id": "att_1", "seq": 3},
        options={"payload": {"provider": "fake"}},
    )
    stream_event = RunStreamEvent.from_runtime_event(runtime_event)
    assert stream_event.schema_version == "1.0"
    assert stream_event.stream_id == "run_1:3"
    assert stream_event.source == "runtime_event"
    assert stream_event.event == "llm_call_started"
    assert stream_event.data["provider"] == "fake"
