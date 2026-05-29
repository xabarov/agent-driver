"""In-memory artifact/context stores."""

from __future__ import annotations

from agent_driver.context.artifacts.protocols import ArtifactStore, ContextStore
from agent_driver.contracts.context import ContextArtifactRef, StoredArtifact


class InMemoryArtifactStore(ArtifactStore):
    """Simple in-memory artifact persistence."""

    def __init__(self) -> None:
        self._by_id: dict[str, StoredArtifact] = {}

    def put(self, artifact: StoredArtifact) -> ContextArtifactRef:
        self._by_id[artifact.ref.artifact_id] = artifact
        return artifact.ref

    def get(self, artifact_id: str) -> StoredArtifact | None:
        return self._by_id.get(artifact_id)

    def list_for_kind(self, kind: str) -> list[StoredArtifact]:
        return [item for item in self._by_id.values() if item.ref.kind.value == kind]


class InMemoryContextStore(ContextStore):
    """Simple in-memory run->artifact reference mapping."""

    def __init__(self) -> None:
        self._refs_by_run: dict[str, list[ContextArtifactRef]] = {}

    def attach_artifact(self, run_id: str, artifact_ref: ContextArtifactRef) -> None:
        self._refs_by_run.setdefault(run_id, []).append(artifact_ref)

    def list_artifacts(self, run_id: str) -> list[ContextArtifactRef]:
        return list(self._refs_by_run.get(run_id, []))
