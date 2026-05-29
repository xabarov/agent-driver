"""Tests for assistant message metadata aggregation."""

from app.services.message_metadata import aggregate_metadata_from_events, merge_metadata


def test_merge_metadata_sums_tokens_and_duration() -> None:
    merged = merge_metadata(
        {"promptTokens": 50, "completionTokens": 100, "durationMs": 1000, "costUsd": 0.01},
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


def test_aggregate_metadata_from_events() -> None:
    events = [
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
            "event": "token_delta",
            "data": {"delta_text": "hi"},
        },
        {
            "event": "llm_call_completed",
            "data": {
                "duration_ms": 1000,
                "usage": {"input_tokens": 5, "output_tokens": 15, "total_tokens": 20},
                "provider": "openrouter",
            },
        },
    ]
    metadata = aggregate_metadata_from_events(events)
    assert metadata["promptTokens"] == 15
    assert metadata["completionTokens"] == 105
    assert metadata["durationMs"] == 3000
    assert metadata["provider"] == "openrouter"
    # No tool_call_completed events → no planning verdict surfaced.
    assert "planningExecuted" not in metadata


def _tool_call_completed(tool_names: list[str]) -> dict[str, object]:
    """Synthesize a tool_call_completed event with the given tool names."""
    return {
        "event": "tool_call_completed",
        "data": {
            "tool_calls": len(tool_names),
            "statuses": ["completed"] * len(tool_names),
            "tools": [{"tool_name": name, "status": "completed"} for name in tool_names],
        },
    }


def test_planning_verdict_engaged_when_plan_and_data_tools_present() -> None:
    """Plan AND data tool ran — verdict 'engaged' (the happy path)."""
    events = [
        _tool_call_completed(["todo_write"]),
        _tool_call_completed(["web_search"]),
    ]
    metadata = aggregate_metadata_from_events(events)
    assert metadata.get("planningExecuted") == "engaged"


def test_planning_verdict_fabricated_when_only_plan_tool_present() -> None:
    """Plan ran but no data tool — verdict 'fabricated' (the D-004 case)."""
    events = [
        _tool_call_completed(["todo_write", "planning_state_update"]),
    ]
    metadata = aggregate_metadata_from_events(events)
    assert metadata.get("planningExecuted") == "fabricated"


def test_planning_verdict_absent_when_no_planning_tool_called() -> None:
    """No planning tool → no verdict in metadata at all (vs explicit null)."""
    events = [_tool_call_completed(["file_read", "web_search"])]
    metadata = aggregate_metadata_from_events(events)
    assert "planningExecuted" not in metadata


def test_planning_verdict_engaged_across_split_events() -> None:
    """Verdict combines across multiple tool_call_completed events."""
    events = [
        _tool_call_completed(["todo_write"]),
        _tool_call_completed(["todo_write"]),
        _tool_call_completed(["web_search"]),
    ]
    metadata = aggregate_metadata_from_events(events)
    assert metadata.get("planningExecuted") == "engaged"
