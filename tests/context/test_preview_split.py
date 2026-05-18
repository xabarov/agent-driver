"""Preview/artifact split helper tests."""

from __future__ import annotations

from agent_driver.context.artifacts import split_preview_and_artifact


def test_split_preview_and_artifact_truncates_long_payload() -> None:
    """Long payload should be truncated in preview and preserved in artifact."""
    preview, artifact = split_preview_and_artifact(
        content="x" * 64,
        max_preview_chars=10,
    )
    assert preview.truncated is True
    assert preview.text.endswith("...")
    assert artifact.content == "x" * 64
    assert artifact.preview is not None


def test_split_preview_and_artifact_keeps_short_payload() -> None:
    """Short payload should be kept as-is without truncation."""
    preview, artifact = split_preview_and_artifact(
        content="short",
        max_preview_chars=10,
    )
    assert preview.truncated is False
    assert preview.text == "short"
    assert artifact.ref.artifact_id.startswith("art_")
