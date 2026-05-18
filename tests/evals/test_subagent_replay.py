"""Replay/support-bundle tests for subagent lifecycle visibility."""

from __future__ import annotations

from agent_driver.contracts import (
    MergeProvenance,
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    SubagentGroup,
    SubagentJoinPolicy,
    SubagentRun,
    SubagentStatus,
    SubagentTerminalState,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.evals import build_support_bundle, render_cli_replay, render_succinct_view


def test_replay_includes_subagent_metadata() -> None:
    """Replay should expose subagent lifecycle summary."""
    output = AgentRunOutput(
        run_id="run_sub_replay",
        attempt_id="att_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        subagent_groups=[
            SubagentGroup(
                group_id="grp_1",
                parent_run_id="run_sub_replay",
                parent_attempt_id="att_1",
                join_policy=SubagentJoinPolicy.WAIT_ALL,
                metadata={"join_state": "joined"},
            )
        ],
        subagent_runs=[
            SubagentRun(
                subagent_run_id="sub_1",
                parent_run_id="run_sub_replay",
                parent_attempt_id="att_1",
                task_id="task_1",
                task_type="analysis",
                description="child",
                status=SubagentStatus.COMPLETED,
                terminal_state=SubagentTerminalState.SUCCEEDED,
                merge_provenance=MergeProvenance(strategy="append", source_kind="child"),
                metadata={"summary": "ok"},
            )
        ],
        metadata={
            "subagent_groups": [{"group_id": "grp_1"}],
            "subagent_runs": [{"subagent_run_id": "sub_1", "status": "completed"}],
        },
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.SUBAGENT_GROUP_STARTED,
                context={"run_id": "run_sub_replay", "attempt_id": "att_1", "seq": 1},
                options={"payload": {"group_id": "grp_1"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.SUBAGENT_GROUP_JOINED,
                context={"run_id": "run_sub_replay", "attempt_id": "att_1", "seq": 2},
                options={"payload": {"group_id": "grp_1", "join_state": "joined"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_sub_replay", "attempt_id": "att_1", "seq": 3},
            ),
        ],
    )
    succinct = render_succinct_view(output)
    assert succinct["event_count"] == 3
    cli = render_cli_replay(output)
    assert "subagent_group_started" in cli
    bundle = build_support_bundle(output)
    assert bundle["run"]["run_id"] == "run_sub_replay"
