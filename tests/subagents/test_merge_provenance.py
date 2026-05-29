"""Subagent merge tests."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentMergeMode,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import MergeProvenance, SubagentRun
from agent_driver.subagents import merge_subagent_outputs


def _completed(sub_id: str, summary: str) -> SubagentRun:
    return SubagentRun(
        subagent_run_id=sub_id,
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        task_id=sub_id,
        task_type="analysis",
        description="d",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.COMPLETED,
        terminal_state=SubagentTerminalState.SUCCEEDED,
        merge_provenance=MergeProvenance(strategy="x", source_kind="y"),
        metadata={"summary": summary},
    )


def test_merge_append_keeps_summary() -> None:
    merged, provenance = merge_subagent_outputs(
        merge_mode=SubagentMergeMode.APPEND,
        runs=[_completed("a", "alpha"), _completed("b", "beta")],
    )
    assert "alpha" in merged
    assert provenance.strategy == "append"


def test_merge_vote_picks_consensus() -> None:
    merged, provenance = merge_subagent_outputs(
        merge_mode=SubagentMergeMode.VOTE,
        runs=[_completed("a", "same"), _completed("b", "same"), _completed("c", "other")],
    )
    assert merged.strip() == "same"
    assert provenance.metadata["mode"] == "vote"
