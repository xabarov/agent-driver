"""Session-memory persistence helpers on top of existing artifact store."""

from __future__ import annotations

from uuid import uuid4

from agent_driver.context.artifacts import ArtifactStore
from agent_driver.contracts import (
    ArtifactKind,
    ContextArtifactRef,
    SensitivityLevel,
    SessionMemory,
    StoredArtifact,
)


def session_memory_artifact_id(session_id: str) -> str:
    """Build deterministic artifact id for one session memory record."""
    return f"session_memory:{session_id}"


def save_session_memory(
    *,
    artifact_store: ArtifactStore,
    memory: SessionMemory,
) -> None:
    """Persist semantic session memory as a memory artifact payload."""
    artifact_store.put(
        StoredArtifact(
            ref=ContextArtifactRef(
                artifact_id=session_memory_artifact_id(memory.session_id),
                kind=ArtifactKind.MEMORY,
                sensitivity=SensitivityLevel.INTERNAL,
            ),
            content=memory.model_dump_json(indent=2),
            metadata={
                "session_id": memory.session_id,
                "memory_id": memory.memory_id,
                "version": memory.version,
                "last_summarized_turn_index": memory.last_summarized_turn_index,
                "store_write_id": f"sm_{uuid4().hex[:8]}",
            },
        )
    )


def load_session_memory(
    *,
    artifact_store: ArtifactStore,
    session_id: str,
) -> SessionMemory | None:
    """Load semantic session memory from artifact store."""
    artifact = artifact_store.get(session_memory_artifact_id(session_id))
    if artifact is None:
        return None
    return SessionMemory.model_validate_json(artifact.content)


__all__ = ["load_session_memory", "save_session_memory", "session_memory_artifact_id"]
