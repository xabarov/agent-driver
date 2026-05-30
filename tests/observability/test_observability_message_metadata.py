"""Tests for reusable assistant message metadata aggregation."""

from __future__ import annotations

from agent_driver.observability import (
    aggregate_message_metadata_from_events,
    merge_message_metadata,
)


def test_merge_message_metadata_sums_tokens_and_duration() -> None:
    merged = merge_message_metadata(
        {
            "promptTokens": 50,
            "completionTokens": 100,
            "durationMs": 1000,
            "costUsd": 0.01,
        },
        {
            "promptTokens": 20,
            "completionTokens": 80,
            "durationMs": 2000,
            "costUsd": 0.005,
        },
    )

    assert merged["promptTokens"] == 70
    assert merged["completionTokens"] == 180
    assert merged["durationMs"] == 3000
    assert merged["costUsd"] == 0.015
    assert merged["tokensPerSecond"] == 60.0


def test_aggregate_message_metadata_from_events() -> None:
    metadata = aggregate_message_metadata_from_events(
        [
            {
                "event": "llm_call_completed",
                "data": {
                    "duration_ms": 2000,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 90,
                        "total_tokens": 100,
                        "cost_usd_estimate": 0.001,
                    },
                },
            },
            {
                "event": "llm_call_completed",
                "data": {
                    "duration_ms": 1000,
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 15,
                        "total_tokens": 20,
                    },
                    "provider": "openrouter",
                },
            },
        ]
    )

    assert metadata["promptTokens"] == 15
    assert metadata["completionTokens"] == 105
    assert metadata["durationMs"] == 3000
    assert metadata["provider"] == "openrouter"
    assert "planningExecuted" not in metadata


def _tool_call_completed(tool_names: list[str]) -> dict[str, object]:
    return {
        "event": "tool_call_completed",
        "data": {
            "tool_calls": len(tool_names),
            "statuses": ["completed"] * len(tool_names),
            "tools": [
                {"tool_name": name, "status": "completed"} for name in tool_names
            ],
        },
    }


def test_planning_verdict_engaged_when_plan_and_data_tools_present() -> None:
    events = [
        _tool_call_completed(["todo_write"]),
        _tool_call_completed(["web_search"]),
    ]

    metadata = aggregate_message_metadata_from_events(events)

    assert metadata.get("planningExecuted") == "engaged"


def test_planning_verdict_fabricated_when_only_plan_tool_present() -> None:
    events = [_tool_call_completed(["todo_write", "planning_state_update"])]

    metadata = aggregate_message_metadata_from_events(events)

    assert metadata.get("planningExecuted") == "fabricated"


def test_planning_verdict_absent_when_no_planning_tool_called() -> None:
    metadata = aggregate_message_metadata_from_events(
        [_tool_call_completed(["file_read", "web_search"])]
    )

    assert "planningExecuted" not in metadata


def test_aggregate_message_metadata_includes_compaction_lifecycle() -> None:
    metadata = aggregate_message_metadata_from_events(
        [
            {
                "event": "memory_compaction_started",
                "data": {
                    "compaction_id": "compact_1",
                    "mode": "partial",
                    "reason": "token_pressure",
                },
            },
            {
                "event": "memory_compacted",
                "data": {
                    "compaction_id": "compact_1",
                    "mode": "partial",
                    "outcome": "success",
                    "summarized_message_count": 9,
                },
            },
        ]
    )

    assert metadata["compaction"] == {
        "status": "done",
        "attempts": 1,
        "compaction_id": "compact_1",
        "mode": "partial",
        "summarized_message_count": 9,
    }
