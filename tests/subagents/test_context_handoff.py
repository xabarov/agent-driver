"""Subagent context handoff tests."""

from __future__ import annotations

from agent_driver.subagents import SubagentTaskSpec, build_child_context_handoff


def test_child_context_handoff_is_bounded_and_audited() -> None:
    """Handoff should keep bounded refs and emit drop audit."""
    task = SubagentTaskSpec(
        task_id="task_1",
        task="investigate",
        description="desc",
        context_refs=("a", "b"),
    )
    handoff, audit = build_child_context_handoff(
        task=task,
        parent_summary="x" * 3000,
        artifact_refs=[{"artifact_id": f"a_{idx}"} for idx in range(12)],
        digest_refs=[{"digest_id": f"d_{idx}"} for idx in range(11)],
        planning_state={"next_plan": "continue"},
        max_refs=5,
    )
    assert len(handoff["artifact_refs"]) == 5
    assert len(handoff["digest_refs"]) == 5
    assert audit["dropped_artifacts"] == 7
    assert audit["dropped_digests"] == 6


def test_child_context_handoff_includes_worker_and_artifact_rules() -> None:
    """Worker handoff should carry bounded scratchpad and artifact policy."""
    task = SubagentTaskSpec(
        task_id="verify_1",
        task="verify",
        description="desc",
        metadata={
            "worker_type": "verifier",
            "required_outputs": ["findings", "risk"],
            "scratchpad": {"max_chars": 1200, "share_with_parent": True},
        },
    )

    handoff, _audit = build_child_context_handoff(
        task=task,
        parent_summary="summary",
        artifact_refs=[],
        digest_refs=[],
        planning_state=None,
    )

    assert handoff["worker"]["type"] == "verifier"
    assert "behavioral regressions" in " ".join(handoff["worker"]["handoff_rules"])
    assert handoff["scratchpad"] == {
        "mode": "bounded_private",
        "max_chars": 1200,
        "share_with_parent": True,
    }
    assert handoff["artifact_handoff"] == {
        "mode": "refs_only",
        "max_refs": 8,
        "required_outputs": ["findings", "risk"],
    }
