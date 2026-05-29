"""Context artifact contracts tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ArtifactPreview,
    ContextArtifactRef,
    SensitivityLevel,
    StoredArtifact,
)
from agent_driver.contracts.enums import ArtifactKind


def test_stored_artifact_with_preview_round_trip() -> None:
    """Stored artifact should support bounded preview + pointer split."""
    artifact = StoredArtifact(
        ref=ContextArtifactRef(
            artifact_id="art_1",
            kind=ArtifactKind.TOOL_RESULT,
            sensitivity=SensitivityLevel.INTERNAL,
        ),
        content="full-content",
        preview=ArtifactPreview(
            text="preview", truncated=True, original_size_bytes=100
        ),
    )
    restored = StoredArtifact.model_validate(artifact.model_dump(mode="json"))
    assert restored.preview is not None
    assert restored.preview.truncated is True


def test_context_artifact_rejects_negative_size() -> None:
    """Artifact ref should reject negative size bytes."""
    with pytest.raises(ValidationError):
        ContextArtifactRef(artifact_id="art_1", kind=ArtifactKind.FILE, size_bytes=-1)
