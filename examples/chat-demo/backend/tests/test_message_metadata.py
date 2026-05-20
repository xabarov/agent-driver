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
