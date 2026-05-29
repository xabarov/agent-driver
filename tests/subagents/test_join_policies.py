"""Subagent join policy tests."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentJoinPolicy,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import SubagentRun
from agent_driver.subagents import evaluate_join_policy


def _run(sub_id: str, status: SubagentStatus) -> SubagentRun:
    terminal = (
        SubagentTerminalState.SUCCEEDED
        if status == SubagentStatus.COMPLETED
        else SubagentTerminalState.FAILED
    )
    if status == SubagentStatus.RUNNING:
        return SubagentRun(
            subagent_run_id=sub_id,
            parent_run_id="run_1",
            parent_attempt_id="att_1",
            task_id=sub_id,
            task_type="analysis",
            description="d",
            execution_mode=SubagentExecutionMode.SYNC,
            fanout_slot=1,
            status=status,
            metadata={},
        )
    return SubagentRun(
        subagent_run_id=sub_id,
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        task_id=sub_id,
        task_type="analysis",
        description="d",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=status,
        terminal_state=terminal if status != SubagentStatus.RUNNING else None,
        merge_provenance=(
            {"strategy": "x", "source_kind": "y"}
            if status == SubagentStatus.COMPLETED
            else None
        ),
        metadata={},
    )


def test_join_wait_all() -> None:
    decision = evaluate_join_policy(
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        runs=[_run("a", SubagentStatus.COMPLETED), _run("b", SubagentStatus.FAILED)],
    )
    assert decision.done is True
    assert decision.state == "joined"


def test_join_k_of_n() -> None:
    decision = evaluate_join_policy(
        join_policy=SubagentJoinPolicy.K_OF_N,
        runs=[
            _run("a", SubagentStatus.COMPLETED),
            _run("b", SubagentStatus.COMPLETED),
            _run("c", SubagentStatus.FAILED),
        ],
        k=2,
    )
    assert decision.done is True


def test_join_manual_review_pending() -> None:
    decision = evaluate_join_policy(
        join_policy=SubagentJoinPolicy.MANUAL_REVIEW,
        runs=[_run("a", SubagentStatus.COMPLETED)],
    )
    assert decision.done is False
    assert decision.state == "manual_review_pending"
