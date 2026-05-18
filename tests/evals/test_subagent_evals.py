"""Deterministic subagent eval-style checks."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentJoinPolicy,
    SubagentMergeMode,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import MergeProvenance, SubagentRun
from agent_driver.subagents import evaluate_join_policy, merge_subagent_outputs


def _run(sub_id: str, status: SubagentStatus, summary: str = "") -> SubagentRun:
    terminal = (
        SubagentTerminalState.SUCCEEDED
        if status == SubagentStatus.COMPLETED
        else SubagentTerminalState.FAILED
    )
    return SubagentRun(
        subagent_run_id=sub_id,
        parent_run_id="run_eval",
        parent_attempt_id="att_eval",
        task_id=sub_id,
        task_type="analysis",
        description="d",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=status,
        terminal_state=terminal if status != SubagentStatus.RUNNING else None,
        merge_provenance=MergeProvenance(strategy="seed", source_kind="child")
        if status == SubagentStatus.COMPLETED
        else None,
        metadata={"summary": summary},
    )


def test_eval_wait_all_and_merge_append() -> None:
    """Wait-all and append merge should produce stable summary and provenance."""
    runs = [_run("a", SubagentStatus.COMPLETED, "one"), _run("b", SubagentStatus.COMPLETED, "two")]
    join = evaluate_join_policy(join_policy=SubagentJoinPolicy.WAIT_ALL, runs=runs)
    merged, provenance = merge_subagent_outputs(merge_mode=SubagentMergeMode.APPEND, runs=runs)
    assert join.done is True
    assert "one" in merged and "two" in merged
    assert provenance.strategy == "append"


def test_eval_best_effort_timeout_path() -> None:
    """Best-effort joins when deadline reached even with partial completion."""
    runs = [_run("a", SubagentStatus.COMPLETED, "ok"), _run("b", SubagentStatus.FAILED, "")]
    join = evaluate_join_policy(
        join_policy=SubagentJoinPolicy.BEST_EFFORT_UNTIL_DEADLINE,
        runs=runs,
        deadline_reached=True,
    )
    assert join.done is True
    assert join.state == "partial_joined"
