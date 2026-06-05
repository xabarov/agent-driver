"""Tests for generic runtime execution proof helpers."""

from __future__ import annotations

from agent_driver.contracts import RunStreamEvent
from agent_driver.runtime import has_real_execution_proof, summarize_execution_proof


def _event(event: str, data: dict) -> RunStreamEvent:
    return RunStreamEvent(
        schema_version="1.0",
        stream_id="run-proof:1",
        run_id="run-proof",
        attempt_id="att-proof",
        seq=1,
        event=event,
        source="runtime_event",
        data=data,
    )


def test_summarize_execution_proof_detects_completed_tool_event() -> None:
    summary = summarize_execution_proof(
        [
            _event("token_delta", {"delta_text": "thinking"}),
            _event(
                "tool_call_completed",
                {"tool_name": "web_search", "status": "completed"},
            ),
        ]
    )

    assert summary.real_execution_proof is True
    assert summary.completed_tool_names == ("web_search",)
    assert summary.completed_tool_count == 1


def test_summarize_execution_proof_supports_aggregated_tools_payload() -> None:
    summary = summarize_execution_proof(
        [
            _event(
                "tool_call_completed",
                {
                    "tools": [
                        {"tool_name": "search", "status": "completed"},
                        {"name": "fetch", "status": "completed"},
                    ]
                },
            )
        ]
    )

    assert summary.completed_tool_names == ("search", "fetch")


def test_has_real_execution_proof_ignores_blocked_or_missing_tool_names() -> None:
    assert (
        has_real_execution_proof(
            [
                _event(
                    "tool_call_completed", {"tool_name": "shell", "status": "blocked"}
                ),
                _event("tool_call_completed", {"status": "completed"}),
            ]
        )
        is False
    )


def test_summarize_execution_proof_accepts_dict_events() -> None:
    summary = summarize_execution_proof(
        [
            {
                "event": "tool_call_completed",
                "data": {"name": "read_file", "status": "completed"},
            }
        ]
    )

    assert summary.real_execution_proof is True
    assert summary.completed_tool_names == ("read_file",)
