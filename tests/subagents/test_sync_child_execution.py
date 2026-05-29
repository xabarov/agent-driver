"""Sync child execution tests."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_driver.contracts import (
    AgentRunOutput,
    ArtifactKind,
    ArtifactRef,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.subagents import (
    InMemorySubagentStore,
    SubagentGroupSpec,
    SubagentTaskSpec,
    execute_subagent_group_background,
    execute_subagent_group_sync,
)
from tests.subagents.parent_handoff import default_parent_handoff


async def _ok_child_runner(run_input):
    return AgentRunOutput(
        run_id=run_input.run_id or "child",
        attempt_id="att_child",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": run_input.run_id or "child",
                    "attempt_id": "att_child",
                    "seq": 1,
                },
            )
        ],
        answer="child answer",
    )


@pytest.mark.asyncio
async def test_sync_child_execution_records_group_and_runs() -> None:
    """Executor should persist group and child runs."""
    store = InMemorySubagentStore()
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1", task="investigate", description="desc"
                ),
            ),
        ),
        store=store,
        child_runner=_ok_child_runner,
        max_child_runs=4,
    )
    assert result.group.group_id == "grp_parent"
    assert result.join_state in {"joined", "race_won", "partial_joined"}
    assert len(result.runs) == 1
    assert result.runs[0].status.value == "completed"


@pytest.mark.asyncio
async def test_sync_child_execution_passes_abort_handle_to_child_runner() -> None:
    """Executor should pass a cascading child abort handle when supported."""
    store = InMemorySubagentStore()
    parent_abort_handle = RunAbortHandle()
    seen = {}

    async def _runner(run_input, *, abort_handle=None):
        seen["run_id"] = run_input.run_id
        seen["abort_handle"] = abort_handle
        return await _ok_child_runner(run_input)

    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        parent_abort_handle=parent_abort_handle,
    )

    assert seen["abort_handle"] is not None
    assert seen["abort_handle"].is_aborted is False
    parent_abort_handle.abort("stop")
    assert seen["abort_handle"].is_aborted is True


@pytest.mark.asyncio
async def test_sync_child_execution_restricts_worker_tool_surface() -> None:
    """Child run policy should be narrowed by worker role definitions."""
    store = InMemorySubagentStore()
    seen = {}

    async def _runner(run_input):
        seen["tool_policy"] = run_input.tool_policy
        return await _ok_child_runner(run_input)

    await execute_subagent_group_sync(
        parent=default_parent_handoff(
            answer="parent summary",
            tool_policy={
                "allowed_tools": ["read_file", "grep_search", "python"],
                "metadata": {"source": "parent"},
            },
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="verify",
                    description="desc",
                    metadata={"worker_type": "verifier"},
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
    )

    assert seen["tool_policy"].allowed_tools == [
        "read_file",
        "grep_search",
        "python",
    ]
    assert seen["tool_policy"].metadata["worker_type"] == "verifier"
    assert seen["tool_policy"].metadata["worker_tool_surface"] == "role_restricted"


@pytest.mark.asyncio
async def test_sync_child_execution_applies_validated_cwd_override(tmp_path) -> None:
    """Child workspace cwd overrides should stay inside parent workspace."""
    store = InMemorySubagentStore()
    child_workspace = tmp_path / "child"
    child_workspace.mkdir()
    seen = {}

    async def _runner(run_input):
        seen["app_metadata"] = run_input.app_metadata
        return await _ok_child_runner(run_input)

    await execute_subagent_group_sync(
        parent=default_parent_handoff(
            answer="parent summary",
            workspace_cwd=str(tmp_path),
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="verify",
                    description="desc",
                    metadata={"cwd": "child"},
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
    )

    assert seen["app_metadata"]["workspace_cwd"] == str(child_workspace.resolve())
    assert seen["app_metadata"]["workspace_cwd_source"] == "subagent_task"


@pytest.mark.asyncio
async def test_sync_child_execution_rejects_cwd_outside_parent_workspace(
    tmp_path,
) -> None:
    """Subagent cwd policy should reject escapes from parent workspace."""
    store = InMemorySubagentStore()
    outside = tmp_path.parent

    with pytest.raises(ValueError, match="outside parent workspace"):
        await execute_subagent_group_sync(
            parent=default_parent_handoff(
                answer="parent summary",
                workspace_cwd=str(tmp_path),
            ),
            group_spec=SubagentGroupSpec(
                group_id="grp_parent",
                purpose="analysis",
                tasks=(
                    SubagentTaskSpec(
                        task_id="task_1",
                        task="verify",
                        description="desc",
                        metadata={"cwd": str(outside)},
                    ),
                ),
            ),
            store=store,
            child_runner=_ok_child_runner,
            max_child_runs=4,
        )


@pytest.mark.asyncio
async def test_sync_child_execution_uses_git_worktree_isolation(tmp_path) -> None:
    """Worktree isolation should keep child writes out of parent workspace."""
    if shutil.which("git") is None:
        pytest.skip("git is required for worktree isolation")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    seen = {}

    async def _runner(run_input):
        workspace = Path(run_input.app_metadata["workspace_cwd"])
        seen["workspace_cwd"] = workspace
        assert workspace != repo.resolve()
        (workspace / "tracked.txt").write_text("child edit", encoding="utf-8")
        return await _ok_child_runner(run_input)

    await execute_subagent_group_sync(
        parent=default_parent_handoff(
            answer="parent summary",
            workspace_cwd=str(repo),
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="edit safely",
                    description="desc",
                    metadata={"isolation_mode": "worktree"},
                ),
            ),
        ),
        store=InMemorySubagentStore(),
        child_runner=_runner,
        max_child_runs=4,
    )

    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "parent\n"
    assert seen["workspace_cwd"].exists() is False


@pytest.mark.asyncio
async def test_sync_child_execution_records_bounded_output_artifact_refs() -> None:
    """Child output artifacts should be bounded and auditable on completed rows."""
    store = InMemorySubagentStore()

    async def _artifact_child(run_input):
        output = await _ok_child_runner(run_input)
        return output.model_copy(
            update={
                "artifacts": [
                    ArtifactRef(
                        artifact_id=f"artifact_{idx}",
                        kind=ArtifactKind.SUBAGENT_OUTPUT,
                        title=f"Artifact {idx}",
                    )
                    for idx in range(12)
                ]
            }
        )

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_artifact_child,
        max_child_runs=4,
    )

    row = result.runs[0]
    assert row.output_pointer is not None
    assert row.output_pointer.artifact_id == "artifact_0"
    assert row.merge_provenance is not None
    assert row.merge_provenance.carried_keys == ["summary", "artifact_refs"]
    assert len(row.metadata["child_artifact_refs"]) == 8
    assert row.metadata["child_artifact_audit"] == {
        "artifact_refs_in": 12,
        "artifact_refs_kept": 8,
        "dropped_artifacts": 4,
    }


@pytest.mark.asyncio
async def test_sync_child_execution_skips_child_when_parent_already_aborted() -> None:
    """Pre-aborted parent should persist cancelled child rows without calling child."""
    store = InMemorySubagentStore()
    parent_abort_handle = RunAbortHandle()
    parent_abort_handle.abort("operator_stop")
    called = False

    async def _runner(_run_input):
        nonlocal called
        called = True
        raise AssertionError("child runner should not be called")

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        parent_abort_handle=parent_abort_handle,
    )

    assert called is False
    assert result.runs[0].status.value == "cancelled"
    assert result.runs[0].terminal_state.value == "cancelled"
    assert result.runs[0].metadata["terminal_reason"] == "operator_stop"


@pytest.mark.asyncio
async def test_background_child_execution_returns_before_child_completes() -> None:
    """Background executor should schedule child work without blocking parent."""
    store = InMemorySubagentStore()
    release_child = asyncio.Event()
    events = []

    async def _runner(run_input):
        await release_child.wait()
        return await _ok_child_runner(run_input)

    result = await execute_subagent_group_background(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_background",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        on_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result.join_state == "background_running"
    assert result.runs[0].execution_mode.value == "background"
    assert store.list_runs("run_parent")[0].status.value == "running"
    assert [event[0] for event in events] == [
        "subagent_group_started",
        "subagent_started",
    ]

    release_child.set()
    for _ in range(20):
        if store.list_runs("run_parent")[0].status.value == "completed":
            break
        await asyncio.sleep(0.01)

    row = store.list_runs("run_parent")[0]
    assert row.status.value == "completed"
    assert store.list_groups("run_parent")[0].metadata["join_state"] == (
        "background_completed"
    )
    assert events[-1][0] == "subagent_completed"


@pytest.mark.asyncio
async def test_background_child_cleanup_completes_group_after_cancelled_child() -> None:
    """Background cleanup should advance groups after cancelled children finish."""
    store = InMemorySubagentStore()
    parent_abort_handle = RunAbortHandle()
    parent_abort_handle.abort("operator_stop")
    called = False

    async def _runner(_run_input):
        nonlocal called
        called = True
        raise AssertionError("cancelled background child should not run")

    result = await execute_subagent_group_background(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_background_cancel",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        parent_abort_handle=parent_abort_handle,
    )

    assert result.join_state == "background_running"
    for _ in range(20):
        rows = store.list_runs("run_parent")
        if rows and rows[0].status.value == "cancelled":
            break
        await asyncio.sleep(0.01)

    rows = store.list_runs("run_parent")
    assert called is False
    assert rows[0].status.value == "cancelled"
    assert rows[0].terminal_state.value == "cancelled"
    assert rows[0].metadata["terminal_reason"] == "operator_stop"
    group = store.list_groups("run_parent")[0]
    assert group.metadata["join_state"] == "background_completed"
    assert group.status.value == "completed"


def _init_git_repo(path: Path) -> None:
    (path / "tracked.txt").write_text("parent\n", encoding="utf-8")
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "tests@example.invalid"],
        ["git", "config", "user.name", "Agent Driver Tests"],
        ["git", "add", "tracked.txt"],
        ["git", "commit", "-m", "seed"],
    ]
    for command in commands:
        subprocess.run(
            command,
            cwd=path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
