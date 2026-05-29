"""Deterministic observation microcompaction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MicrocompactionResult:
    """Result of deterministic observation microcompaction."""

    observations: list[dict[str, Any]]
    audit: list[dict[str, Any]] = field(default_factory=list)
    bytes_saved: int = 0
    estimated_tokens_saved: int = 0


def _tool_call_id(row: dict[str, Any]) -> str:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        return ""
    return str(provenance.get("tool_call_id", "") or "")


def _compact_observation_row(
    row: dict[str, Any], *, max_preview_chars: int
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    """Compact one observation row if it exceeds preview budget."""
    preview = row.get("text_preview")
    if not isinstance(preview, str) or len(preview) <= max_preview_chars:
        return row, None, 0
    provenance = row.get("provenance")
    source = (
        str(provenance.get("source", "observation"))
        if isinstance(provenance, dict)
        else "observation"
    )
    replacement = (
        f"[{source}] output compacted; see artifact/context references for details."
    )
    old_len = len(preview)
    new_len = len(replacement)
    if old_len <= new_len:
        return row, None, 0
    updated = dict(row)
    updated["text_preview"] = replacement
    updated["truncated"] = True
    metadata = updated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    enriched = dict(metadata)
    enriched["microcompacted"] = True
    enriched["microcompaction_original_length"] = old_len
    enriched["microcompaction_replacement_length"] = new_len
    updated["metadata"] = enriched
    saved = old_len - new_len
    audit = {
        "observation_id": str(updated.get("observation_id", "")),
        "tool_call_id": _tool_call_id(updated),
        "reason": "old_large_observation",
        "saved_chars": saved,
    }
    return updated, audit, saved


def microcompact_observations(
    observations: list[dict[str, Any]],
    *,
    preserve_recent: int = 6,
    max_preview_chars: int = 180,
) -> MicrocompactionResult:
    """Replace older large observation previews with deterministic stubs."""
    if not observations:
        return MicrocompactionResult(observations=[])

    compacted = [dict(item) for item in observations]
    keep_from = max(0, len(compacted) - max(0, preserve_recent))
    audit: list[dict[str, Any]] = []
    bytes_saved = 0

    for index in range(keep_from):
        row, audit_item, saved = _compact_observation_row(
            compacted[index], max_preview_chars=max_preview_chars
        )
        compacted[index] = row
        if audit_item is None:
            continue
        bytes_saved += saved
        audit.append(audit_item)

    return MicrocompactionResult(
        observations=compacted,
        audit=audit,
        bytes_saved=bytes_saved,
        estimated_tokens_saved=max(0, bytes_saved // 4),
    )


__all__ = ["MicrocompactionResult", "microcompact_observations"]
