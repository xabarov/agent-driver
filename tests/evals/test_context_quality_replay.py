"""Replay assertions for context-quality baseline channels."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.contracts.memory import MemoryProjection, MemoryStep
from agent_driver.evals import (
    evaluate_baseline_strategies,
    render_cli_replay,
    render_succinct_view,
)


def test_context_quality_replay_exposes_decision_channels() -> None:
    """Replay view should expose planning and context-hygiene channels."""
    output = AgentRunOutput(
        run_id="run_phase8_baseline",
        attempt_id="att_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        memory_projection=MemoryProjection(
            run_id="run_phase8_baseline",
            attempt_id="att_1",
            view="succinct",
            steps=[MemoryStep(step_index=0, kind="task", content="baseline replay")],
            metadata={
                "planning_channel": "present",
                "token_pressure": {"state": "early_warning"},
                "trim_audit_size": 2,
                "microcompaction_audit_size": 1,
            },
        ),
        metadata={
            "token_pressure": {"state": "early_warning"},
            "trim_audit": [{"record_id": "trim_1"}],
            "microcompaction_audit": [{"record_id": "micro_1"}],
            "planning_state": {"run_id": "run_phase8_baseline"},
        },
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={
                    "run_id": "run_phase8_baseline",
                    "attempt_id": "att_1",
                    "seq": 1,
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_phase8_baseline",
                    "attempt_id": "att_1",
                    "seq": 2,
                },
            ),
        ],
    )
    view = render_succinct_view(output)
    cli = render_cli_replay(output)
    assert view["event_count"] == 2
    assert view["token_pressure"]["state"] == "early_warning"
    assert view["trim_audit_size"] == 1
    assert view["microcompaction_audit_size"] == 1
    assert view["has_planning_state"] is True
    assert "token_pressure={'state': 'early_warning'}" in cli
    assert "trim_audit_size=1" in cli
    assert "microcompaction_audit_size=1" in cli


def test_context_quality_baseline_keeps_micro_above_trim() -> None:
    """Microcompaction baseline should not regress relative recall."""
    baseline = evaluate_baseline_strategies()
    assert (
        baseline["trim_plus_microcompaction"]["fact_recall"]
        >= baseline["trim_only"]["fact_recall"]
    )
