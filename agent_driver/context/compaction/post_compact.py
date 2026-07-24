"""Post-compaction cleanup and reinjection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PostCompactCleanupResult:
    """Result summary for post-compaction cleanup."""

    cleaned_keys: tuple[str, ...]
    reinjected_keys: tuple[str, ...]


# Epic 035 D: bound on the recalled-memory block re-injected after compaction so a
# large recall can't defeat the compaction it rides through (hermes todo bound).
_MAX_REINJECTED_RECALL_CHARS = 2000


def apply_post_compact_cleanup(
    *,
    metadata: dict[str, Any],
    max_reinjected_artifact_refs: int = 5,
) -> PostCompactCleanupResult:
    """Clear stale compaction side-state and keep bounded active context.

    Epic 035 phase D — the SINGLE point that keeps steering state alive across a
    compaction that rewrites the prompt, instead of per-feature crutches (hermes
    «the working list is re-injected after every compaction event»). Today
    planning-state and artifact-refs re-inject here; this also re-injects the
    goal-gate rubric snapshot and the recalled-memory block, which previously
    survived only incidentally (compaction leaves ``context.metadata`` in place but
    the rewritten message list could drop the system block carrying them).
    """
    cleaned: list[str] = []
    for key in ("microcompaction", "microcompaction_audit"):
        if key in metadata:
            cleaned.append(key)
        metadata.pop(key, None)

    reinjected: list[str] = []
    planning_state = metadata.get("planning_state")
    if isinstance(planning_state, dict):
        metadata["planning_state_reinjected"] = planning_state
        reinjected.append("planning_state_reinjected")

    artifact_refs = metadata.get("artifact_refs")
    if isinstance(artifact_refs, list):
        bounded_refs = [item for item in artifact_refs if isinstance(item, dict)][
            :max_reinjected_artifact_refs
        ]
        metadata["artifact_refs_reinjected"] = bounded_refs
        reinjected.append("artifact_refs_reinjected")

    # Goal-gate rubric: a compact raw-free snapshot (iteration count + latest
    # verdict) so the revision loop keeps its criterion after the prompt rewrite.
    rubric_iterations = metadata.get("rubric_iterations")
    rubric_evaluations = metadata.get("rubric_evaluations")
    if isinstance(rubric_iterations, int) and rubric_iterations > 0:
        latest = (
            rubric_evaluations[-1]
            if isinstance(rubric_evaluations, list) and rubric_evaluations
            else None
        )
        metadata["rubric_reinjected"] = {
            "iterations": rubric_iterations,
            "latest_evaluation": latest if isinstance(latest, dict) else None,
        }
        reinjected.append("rubric_reinjected")

    # Recalled long-term memory (epic 021): the reference block the system prompt
    # carries. Bound it so a big recall doesn't defeat the compaction.
    recalled = metadata.get("recalled_memory")
    if isinstance(recalled, str) and recalled.strip():
        metadata["recalled_memory_reinjected"] = recalled[:_MAX_REINJECTED_RECALL_CHARS]
        reinjected.append("recalled_memory_reinjected")

    return PostCompactCleanupResult(
        cleaned_keys=tuple(cleaned),
        reinjected_keys=tuple(reinjected),
    )


__all__ = ["PostCompactCleanupResult", "apply_post_compact_cleanup"]
