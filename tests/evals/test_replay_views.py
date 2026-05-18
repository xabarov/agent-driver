"""Replay/debug view and support bundle tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.evals import (
    build_support_bundle,
    render_cli_replay,
    render_full_debug_view,
    render_succinct_view,
)


def _output() -> AgentRunOutput:
    return AgentRunOutput(
        run_id="run_replay_1",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={"run_id": "run_replay_1", "attempt_id": "attempt_1", "seq": 1},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_replay_1", "attempt_id": "attempt_1", "seq": 2},
            ),
        ],
    )


def test_render_views_have_expected_shape() -> None:
    """Replay projections should provide full and succinct representations."""
    payload = _output()
    full_view = render_full_debug_view(payload)
    succinct = render_succinct_view(payload)
    cli = render_cli_replay(payload)
    assert full_view["status"] == "completed"
    assert succinct["event_count"] == 2
    assert "run_replay_1" in cli


def test_support_bundle_contains_trace_and_redaction_metadata() -> None:
    """Support bundle should include trace export and redaction contract."""
    payload = _output()
    bundle = build_support_bundle(payload)
    assert bundle["run"]["run_id"] == "run_replay_1"
    assert bundle["trace"]["run_id"] == "run_replay_1"
    assert bundle["redaction"]["safe_by_default"] is True
