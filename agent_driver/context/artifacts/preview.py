"""Helpers for splitting long tool outputs into preview + artifact."""

from __future__ import annotations

from uuid import uuid4

from agent_driver.contracts.context import (
    ArtifactPreview,
    ContextArtifactRef,
    StoredArtifact,
)
from agent_driver.contracts.enums import ArtifactKind, SensitivityLevel


def split_preview_and_artifact(
    *,
    content: str,
    max_preview_chars: int,
    kind: ArtifactKind = ArtifactKind.TOOL_RESULT,
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL,
) -> tuple[ArtifactPreview, StoredArtifact]:
    """Split raw content into bounded preview and stored artifact payload."""
    truncated = len(content) > max_preview_chars
    preview_text = content[:max_preview_chars] if truncated else content
    preview = ArtifactPreview(
        text=preview_text + ("..." if truncated else ""),
        truncated=truncated,
        original_size_bytes=len(content.encode("utf-8")),
        metadata={"max_preview_chars": max_preview_chars},
    )
    ref = ContextArtifactRef(
        artifact_id=f"art_{uuid4().hex}",
        kind=kind,
        size_bytes=len(content.encode("utf-8")),
        sensitivity=sensitivity,
    )
    stored = StoredArtifact(
        ref=ref,
        content=content,
        preview=preview,
        metadata={"split": "preview_artifact"},
    )
    return preview, stored
