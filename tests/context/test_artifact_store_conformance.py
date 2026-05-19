"""Conformance tests for artifact/context stores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_driver.context.artifacts import (
    InMemoryArtifactStore,
    InMemoryContextStore,
    SqliteArtifactStore,
    SqliteContextStore,
)
from agent_driver.context.artifacts.protocols import ArtifactStore, ContextStore
from agent_driver.contracts import ContextArtifactRef, SensitivityLevel, StoredArtifact
from agent_driver.contracts.enums import ArtifactKind


@dataclass(frozen=True)
class _Backend:
    artifact_store: ArtifactStore
    context_store: ContextStore


def _backend(name: str, tmp_path: Path) -> _Backend:
    if name == "memory":
        return _Backend(
            artifact_store=InMemoryArtifactStore(),
            context_store=InMemoryContextStore(),
        )
    if name == "sqlite":
        db = str(tmp_path / "context_artifacts.db")
        return _Backend(
            artifact_store=SqliteArtifactStore(path=db),
            context_store=SqliteContextStore(path=db),
        )
    raise ValueError(f"Unsupported backend '{name}'")


@pytest.mark.parametrize("backend_name", ["memory", "sqlite"])
def test_artifact_store_and_context_mapping(tmp_path: Path, backend_name: str) -> None:
    """Backends should persist artifacts and run-level artifact refs."""
    backend = _backend(backend_name, tmp_path)
    ref = ContextArtifactRef(
        artifact_id="art_1",
        kind=ArtifactKind.TOOL_RESULT,
        sensitivity=SensitivityLevel.INTERNAL,
    )
    artifact = StoredArtifact(ref=ref, content="full-content")
    persisted_ref = backend.artifact_store.put(artifact)
    backend.context_store.attach_artifact("run_1", persisted_ref)
    loaded = backend.artifact_store.get("art_1")
    refs = backend.context_store.list_artifacts("run_1")
    assert loaded is not None
    assert loaded.content == "full-content"
    assert refs[0].artifact_id == "art_1"
