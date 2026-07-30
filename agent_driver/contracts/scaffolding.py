"""Canonical tag for runtime-synthesized (scaffolding) chat messages (epic 043 C).

The runtime injects synthetic USER-role turns into history — parse-error repair
hints, denial/unknown-tool recovery, forced-final nudges, todo re-injections.
They are NOT human intent. The reference incident (hermes 923704c7c): a synthetic
nudge added without a scaffolding tag replayed after resume as user-authored
context, and the compressor read it as the human's request.

One tag, honored in three places simultaneously:
  * persistence — a resumed transcript can tell scaffolding from a real user turn;
  * compaction — the summarizer never folds scaffolding into "user intent";
  * display  — hosts can filter runtime scaffolding out of the visible transcript.

``is_scaffolding`` accepts a :class:`ChatMessage`, a serialized message dict
(``model_dump``), or a bare metadata mapping, so every layer calls one predicate.
"""

from __future__ import annotations

from typing import Any, Mapping

# Metadata key carrying the scaffolding kind (a short slug, e.g. "todo_reminder").
SCAFFOLDING_METADATA_KEY = "runtime_scaffolding"


def scaffolding_metadata(
    kind: str, *, base: Mapping[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    """Return a metadata dict tagged as runtime scaffolding of ``kind``.

    ``base`` merges in any existing metadata; ``extra`` adds sibling keys.
    """
    metadata: dict[str, Any] = dict(base or {})
    metadata[SCAFFOLDING_METADATA_KEY] = kind
    metadata.update(extra)
    return metadata


def _metadata_of(candidate: Any) -> Mapping[str, Any] | None:
    metadata = getattr(candidate, "metadata", None)
    if isinstance(metadata, Mapping):
        return metadata
    if isinstance(candidate, Mapping):
        inner = candidate.get("metadata")
        if isinstance(inner, Mapping):
            return inner
        # A bare metadata mapping passed directly.
        if SCAFFOLDING_METADATA_KEY in candidate:
            return candidate
    return None


def is_scaffolding(candidate: Any) -> bool:
    """Return whether a message / message-dict / metadata carries the tag."""
    metadata = _metadata_of(candidate)
    if metadata is None:
        return False
    return bool(metadata.get(SCAFFOLDING_METADATA_KEY))


def scaffolding_kind(candidate: Any) -> str | None:
    """Return the scaffolding kind slug, or ``None`` when not scaffolding."""
    metadata = _metadata_of(candidate)
    if metadata is None:
        return None
    value = metadata.get(SCAFFOLDING_METADATA_KEY)
    return value if isinstance(value, str) and value else None


__all__ = [
    "SCAFFOLDING_METADATA_KEY",
    "is_scaffolding",
    "scaffolding_kind",
    "scaffolding_metadata",
]
