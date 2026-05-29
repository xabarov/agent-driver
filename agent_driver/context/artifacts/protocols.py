"""Artifact/context store protocols for Phase-6."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.context import ContextArtifactRef, StoredArtifact


class ArtifactStore(Protocol):
    """Protocol for persisting large artifact payloads with references."""

    def put(self, artifact: StoredArtifact) -> ContextArtifactRef:
        """Persist artifact payload and return reference."""
        raise NotImplementedError

    def get(self, artifact_id: str) -> StoredArtifact | None:
        """Load stored artifact by identifier."""
        raise NotImplementedError

    def list_for_kind(self, kind: str) -> list[StoredArtifact]:
        """List artifacts by kind."""
        raise NotImplementedError


class ContextStore(Protocol):
    """Protocol for mapping run/session context to artifact refs."""

    def attach_artifact(self, run_id: str, artifact_ref: ContextArtifactRef) -> None:
        """Attach artifact reference to a run context."""
        raise NotImplementedError

    def list_artifacts(self, run_id: str) -> list[ContextArtifactRef]:
        """List artifact references attached to run context."""
        raise NotImplementedError
