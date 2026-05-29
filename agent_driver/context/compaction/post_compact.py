"""Post-compaction cleanup and reinjection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PostCompactCleanupResult:
    """Result summary for post-compaction cleanup."""

    cleaned_keys: tuple[str, ...]
    reinjected_keys: tuple[str, ...]


def apply_post_compact_cleanup(
    *,
    metadata: dict[str, Any],
    max_reinjected_artifact_refs: int = 5,
) -> PostCompactCleanupResult:
    """Clear stale compaction side-state and keep bounded active context."""
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
        bounded_refs = [
            item for item in artifact_refs if isinstance(item, dict)
        ][:max_reinjected_artifact_refs]
        metadata["artifact_refs_reinjected"] = bounded_refs
        reinjected.append("artifact_refs_reinjected")

    return PostCompactCleanupResult(
        cleaned_keys=tuple(cleaned),
        reinjected_keys=tuple(reinjected),
    )


__all__ = ["PostCompactCleanupResult", "apply_post_compact_cleanup"]
