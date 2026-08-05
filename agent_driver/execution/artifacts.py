"""Bridge backend execution artifacts into Agent Driver's artifact vocabulary.

A backend that produces large output spills it to its own store and returns an
:class:`~agent_driver.contracts.execution.ArtifactRef` (content-addressed:
``digest`` + ``size_bytes``). EPIC-03 WP-D maps that into the existing context
artifact reference and a bounded, model-facing payload so:

- the digest and size are preserved (a reference is not proof of inspection);
- only a bounded preview enters model context — never an implicit full-content
  load (the full content stays in the backend, fetchable on demand).
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.context import ContextArtifactRef
from agent_driver.contracts.enums import ArtifactKind
from agent_driver.contracts.execution import ArtifactRef

# Default bounded preview cap for a bridged artifact (matches the spill path's
# 2 KB convention so model observations stay short).
ARTIFACT_PREVIEW_MAX_CHARS = 2000


def execution_artifact_to_context_ref(artifact: ArtifactRef) -> ContextArtifactRef:
    """Map a backend execution ``ArtifactRef`` to a ``ContextArtifactRef``.

    The content-address ``digest`` (and ``media_type``/``backend_id``/
    ``execution_id``) is preserved in ``metadata`` since ``ContextArtifactRef``
    has no digest field. Size carries across directly.
    """
    metadata: dict[str, Any] = {"digest": artifact.digest}
    if artifact.media_type is not None:
        metadata["media_type"] = artifact.media_type
    if artifact.backend_id is not None:
        metadata["backend_id"] = artifact.backend_id
    if artifact.execution_id is not None:
        metadata["execution_id"] = artifact.execution_id
    return ContextArtifactRef(
        artifact_id=artifact.artifact_id,
        kind=ArtifactKind.TOOL_RESULT,
        size_bytes=artifact.size_bytes,
        metadata=metadata,
    )


def execution_artifact_reference_payload(
    artifact: ArtifactRef,
    *,
    preview: str = "",
    max_preview_chars: int = ARTIFACT_PREVIEW_MAX_CHARS,
) -> dict[str, Any]:
    """A bounded, model-facing payload for a backend-produced artifact.

    Carries the identity (id + digest + size) and a bounded preview only — never
    the full content. The model can fetch the full artifact from the backend on
    demand rather than having it forced into context.
    """
    truncated = len(preview) > max_preview_chars
    return {
        "summary": "<persisted-output>",
        "persisted": True,
        "artifact_id": artifact.artifact_id,
        "digest": artifact.digest,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "backend_id": artifact.backend_id,
        "preview": preview[:max_preview_chars],
        "preview_truncated": truncated,
    }


__all__ = [
    "ARTIFACT_PREVIEW_MAX_CHARS",
    "execution_artifact_to_context_ref",
    "execution_artifact_reference_payload",
]
