"""Epic 035 D: single-point re-inject invariant after compaction."""

from __future__ import annotations

from agent_driver.context.compaction.post_compact import apply_post_compact_cleanup


def test_reinjects_all_steering_state() -> None:
    meta = {
        "microcompaction": {"x": 1},
        "microcompaction_audit": {"y": 2},
        "planning_state": {"todos": ["a"]},
        "artifact_refs": [{"artifact_id": f"a{i}"} for i in range(9)],
        "rubric_iterations": 2,
        "rubric_evaluations": [
            {"satisfied": False, "feedback": "нет свода"},
            {"satisfied": True, "feedback": ""},
        ],
        "recalled_memory": "Пользователь любит краткие ответы.",
    }
    result = apply_post_compact_cleanup(metadata=meta, max_reinjected_artifact_refs=5)

    # Stale microcompaction cleared.
    assert set(result.cleaned_keys) == {"microcompaction", "microcompaction_audit"}
    assert "microcompaction" not in meta

    # All four steering planes re-injected.
    assert set(result.reinjected_keys) == {
        "planning_state_reinjected",
        "artifact_refs_reinjected",
        "rubric_reinjected",
        "recalled_memory_reinjected",
    }
    assert meta["planning_state_reinjected"] == {"todos": ["a"]}
    assert len(meta["artifact_refs_reinjected"]) == 5  # bounded
    assert meta["rubric_reinjected"] == {
        "iterations": 2,
        "latest_evaluation": {"satisfied": True, "feedback": ""},
    }
    assert meta["recalled_memory_reinjected"] == "Пользователь любит краткие ответы."


def test_reinject_bounds_large_recall() -> None:
    meta = {"recalled_memory": "x" * 5000}
    apply_post_compact_cleanup(metadata=meta)
    assert len(meta["recalled_memory_reinjected"]) == 2000


def test_absent_state_reinjects_nothing() -> None:
    meta: dict = {}
    result = apply_post_compact_cleanup(metadata=meta)
    assert result.reinjected_keys == ()
    # rubric with 0 iterations does not re-inject.
    meta2 = {"rubric_iterations": 0}
    assert apply_post_compact_cleanup(metadata=meta2).reinjected_keys == ()
