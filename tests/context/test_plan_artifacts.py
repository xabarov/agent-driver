"""Plan artifact lifecycle tests."""

from __future__ import annotations

import pytest

from agent_driver.context.planning import (
    InMemoryPlanArtifactStore,
    SqlitePlanArtifactStore,
    approve_plan_artifact,
    create_plan_artifact,
    mark_plan_awaiting_approval,
    plan_content_hash,
    reject_plan_artifact,
    update_plan_artifact_content,
)
from agent_driver.contracts import PlanningModeState


@pytest.fixture(params=["memory", "sqlite"])
def artifact_store(request, tmp_path):
    """Run plan artifact store behavior tests against all local stores."""
    if request.param == "sqlite":
        return SqlitePlanArtifactStore(path=str(tmp_path / "plans.db"))
    return InMemoryPlanArtifactStore()


def test_plan_artifact_lifecycle_hash_and_status() -> None:
    """Plan artifacts should move through collecting/approval states."""
    artifact = create_plan_artifact(
        plan_id="plan_1",
        run_id="run_1",
        thread_id="thread_1",
        agent_id="agent",
        content="Initial plan",
        path="/tmp/plan.md",
    )
    assert artifact.status == PlanningModeState.COLLECTING
    assert artifact.content_hash == plan_content_hash("Initial plan")

    updated = update_plan_artifact_content(artifact, content="Updated plan")
    assert updated.content == "Updated plan"
    assert updated.content_hash == plan_content_hash("Updated plan")
    assert updated.approved_at is None

    awaiting = mark_plan_awaiting_approval(updated)
    assert awaiting.status == PlanningModeState.AWAITING_APPROVAL

    approved = approve_plan_artifact(awaiting, approved_by="roman")
    assert approved.status == PlanningModeState.APPROVED
    assert approved.approved_at is not None
    assert approved.approved_by == "roman"

    rejected = reject_plan_artifact(awaiting, rejected_by="roman", reason="revise")
    assert rejected.status == PlanningModeState.REJECTED
    assert rejected.rejected_at is not None
    assert rejected.rejection_reason == "revise"


def test_plan_artifact_store_lists_by_run(artifact_store) -> None:
    """Plan artifact stores should preserve insertion order per run."""
    store = artifact_store
    first = create_plan_artifact(
        plan_id="plan_1", run_id="run_1", agent_id="agent", content="A"
    )
    second = create_plan_artifact(
        plan_id="plan_2", run_id="run_1", agent_id="agent", content="B"
    )
    other = create_plan_artifact(
        plan_id="plan_3", run_id="run_2", agent_id="agent", content="C"
    )
    store.put(first)
    store.put(second)
    store.put(other)

    assert store.get("plan_1") == first
    assert [item.plan_id for item in store.list_for_run("run_1")] == [
        "plan_1",
        "plan_2",
    ]


def test_sqlite_plan_artifact_store_persists_rows(tmp_path) -> None:
    """SQLite plan artifacts should survive store re-instantiation."""
    path = tmp_path / "plans.db"
    first_store = SqlitePlanArtifactStore(path=str(path))
    artifact = create_plan_artifact(
        plan_id="plan_persist",
        run_id="run_persist",
        agent_id="agent",
        content="Persistent plan",
    )
    first_store.put(mark_plan_awaiting_approval(artifact))

    second_store = SqlitePlanArtifactStore(path=str(path))

    loaded = second_store.get("plan_persist")
    assert loaded is not None
    assert loaded.content == "Persistent plan"
    assert loaded.status == PlanningModeState.AWAITING_APPROVAL
    assert [item.plan_id for item in second_store.list_for_run("run_persist")] == [
        "plan_persist"
    ]
