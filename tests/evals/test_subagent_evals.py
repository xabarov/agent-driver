"""Deterministic subagent eval-style checks."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentJoinPolicy,
    SubagentMergeMode,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import MergeProvenance, SubagentRun
from agent_driver.subagents import (
    InMemorySubagentMailboxStore,
    InMemorySubagentStore,
    SubagentGroupSpec,
    SubagentTaskSpec,
    append_subagent_continuation,
    evaluate_join_policy,
    execute_subagent_group_sync,
    merge_subagent_outputs,
)
from tests.subagents.parent_handoff import default_parent_handoff


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
        merge_provenance=(
            MergeProvenance(strategy="seed", source_kind="child")
            if status == SubagentStatus.COMPLETED
            else None
        ),
        metadata={"summary": summary},
    )


def test_eval_wait_all_and_merge_append() -> None:
    """Wait-all and append merge should produce stable summary and provenance."""
    runs = [
        _run("a", SubagentStatus.COMPLETED, "one"),
        _run("b", SubagentStatus.COMPLETED, "two"),
    ]
    join = evaluate_join_policy(join_policy=SubagentJoinPolicy.WAIT_ALL, runs=runs)
    merged, provenance = merge_subagent_outputs(
        merge_mode=SubagentMergeMode.APPEND, runs=runs
    )
    assert join.done is True
    assert "one" in merged and "two" in merged
    assert provenance.strategy == "append"


def test_eval_best_effort_timeout_path() -> None:
    """Best-effort joins when deadline reached even with partial completion."""
    runs = [
        _run("a", SubagentStatus.COMPLETED, "ok"),
        _run("b", SubagentStatus.FAILED, ""),
    ]
    join = evaluate_join_policy(
        join_policy=SubagentJoinPolicy.BEST_EFFORT_UNTIL_DEADLINE,
        runs=runs,
        deadline_reached=True,
    )
    assert join.done is True
    assert join.state == "partial_joined"


@pytest.mark.asyncio
async def test_eval_research_fanout_preserves_role_tool_surfaces() -> None:
    """Research fan-out should run independent workers with narrowed tools."""
    store = InMemorySubagentStore()
    seen_policies = []

    async def _research_child(run_input):
        seen_policies.append(run_input.tool_policy)
        return _output(run_input.run_id or "child", f"source for {run_input.input}")

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(
            tool_policy={
                "allowed_tools": [
                    "web_search",
                    "web_fetch",
                    "read_file",
                    "grep_search",
                    "python",
                ]
            }
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_research_eval",
            purpose="research_fanout",
            join_policy=SubagentJoinPolicy.WAIT_ALL,
            merge_mode=SubagentMergeMode.APPEND,
            tasks=(
                SubagentTaskSpec(
                    task_id="source_a",
                    task="compare docs",
                    description="Research official docs",
                    metadata={"worker_type": "researcher"},
                ),
                SubagentTaskSpec(
                    task_id="source_b",
                    task="compare implementation",
                    description="Research local implementation",
                    metadata={"worker_type": "researcher"},
                ),
            ),
        ),
        store=store,
        child_runner=_research_child,
        max_child_runs=4,
    )

    assert result.join_state == "joined"
    assert len(result.runs) == 2
    assert all(
        policy.metadata["worker_type"] == "researcher" for policy in seen_policies
    )
    assert all("python" not in policy.allowed_tools for policy in seen_policies)
    assert "source for compare docs" in result.merged_summary
    assert "source for compare implementation" in result.merged_summary


def test_eval_corrected_continuation_targets_existing_child() -> None:
    """Corrected continuation should append to the existing child mailbox."""
    store = InMemorySubagentStore()
    mailbox = InMemorySubagentMailboxStore()
    row = _run("sub_research", SubagentStatus.RUNNING)
    store.upsert_run(
        row.model_copy(update={"metadata": {"handoff": {"task_id": "source_a"}}})
    )

    updated = append_subagent_continuation(
        store,
        parent_run_id="run_eval",
        subagent_run_id="sub_research",
        message="Correction: ignore stale source, use the current API docs.",
        metadata={"kind": "correction"},
        mailbox_store=mailbox,
    )

    assert updated is not None
    assert updated.metadata["handoff"]["task_id"] == "source_a"
    assert updated.metadata["continuation_messages"][0]["metadata"] == {
        "kind": "correction"
    }
    assert mailbox.list_pending(parent_run_id="run_eval")[0].payload == {
        "message": "Correction: ignore stale source, use the current API docs."
    }


@pytest.mark.asyncio
async def test_eval_verifier_catch_survives_merge() -> None:
    """Verifier critique should be preserved beside implementer output."""
    store = InMemorySubagentStore()

    async def _role_child(run_input):
        worker_type = run_input.tool_policy.metadata["worker_type"]
        if worker_type == "verifier":
            return _output(run_input.run_id or "child", "bug: missing regression test")
        return _output(run_input.run_id or "child", "implemented endpoint")

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(run_id="run_eval"),
        group_spec=SubagentGroupSpec(
            group_id="grp_verify_eval",
            purpose="implement_then_verify",
            join_policy=SubagentJoinPolicy.WAIT_ALL,
            merge_mode=SubagentMergeMode.APPEND,
            tasks=(
                SubagentTaskSpec(
                    task_id="impl",
                    task="implement endpoint",
                    description="Implement endpoint",
                    metadata={"worker_type": "implementer"},
                ),
                SubagentTaskSpec(
                    task_id="verify",
                    task="verify endpoint",
                    description="Verify endpoint",
                    metadata={"worker_type": "verifier"},
                ),
            ),
        ),
        store=store,
        child_runner=_role_child,
        max_child_runs=4,
    )

    assert "implemented endpoint" in result.merged_summary
    assert "bug: missing regression test" in result.merged_summary
    verifier_run = [
        row
        for row in store.list_runs("run_eval")
        if row.metadata["handoff"]["worker"]["type"] == "verifier"
    ][0]
    assert "behavioral regressions" in " ".join(
        verifier_run.metadata["handoff"]["worker"]["handoff_rules"]
    )


def _output(run_id: str, answer: str) -> AgentRunOutput:
    return AgentRunOutput(
        run_id=run_id,
        attempt_id="att_child",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": run_id, "attempt_id": "att_child", "seq": 1},
            )
        ],
        answer=answer,
    )
