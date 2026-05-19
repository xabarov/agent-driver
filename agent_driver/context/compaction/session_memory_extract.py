"""Deterministic session-memory extraction from stored turn digests."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.context import SessionMemory, TurnDigest


@dataclass(frozen=True, slots=True)
class SessionMemoryExtractionResult:
    """Extraction status payload for runtime metadata/audit."""

    updated: bool
    reason: str
    memory: SessionMemory | None = None
    considered_digest_ids: tuple[str, ...] = ()


def extract_session_memory(
    *,
    session_id: str,
    digests: list[TurnDigest],
    previous: SessionMemory | None,
    min_turn_gap: int = 2,
    max_summary_digests: int = 6,
) -> SessionMemoryExtractionResult:
    """Build or refresh session memory from newest turn digests."""
    if not digests:
        return SessionMemoryExtractionResult(
            updated=False,
            reason="no_digests",
        )
    latest_turn = digests[-1].turn_index
    previous_turn = previous.last_summarized_turn_index if previous else -1
    if latest_turn - previous_turn < min_turn_gap:
        return SessionMemoryExtractionResult(
            updated=False,
            reason="turn_gap_below_threshold",
        )
    new_digests = [item for item in digests if item.turn_index > previous_turn]
    if not new_digests:
        return SessionMemoryExtractionResult(updated=False, reason="no_new_digests")
    window = new_digests[-max_summary_digests:]
    summary = " | ".join(
        item.summary.strip()
        for item in window
        if item.summary and item.summary.strip()
    )[:1200] or "Session summary unavailable"
    source_digest_ids = [item.digest_id for item in window]
    source_artifact_ids = sorted(
        {
            reference
            for item in window
            for reference in item.references
            if isinstance(reference, str) and reference
        }
    )
    key_facts = _collect_unique_lines(summary, limit=8)
    memory_id = previous.memory_id if previous else f"sm_{session_id}"
    memory = SessionMemory(
        memory_id=memory_id,
        session_id=session_id,
        version=(previous.version + 1) if previous else 1,
        summary=summary,
        key_facts=key_facts,
        pending_tasks=_extract_pending_tasks(window),
        open_questions=_extract_open_questions(window),
        last_summarized_turn_index=latest_turn,
        source_digest_ids=source_digest_ids,
        source_artifact_ids=source_artifact_ids,
        metadata={
            "extractor": "deterministic_digest",
            "min_turn_gap": min_turn_gap,
            "digests_considered": len(window),
        },
    )
    return SessionMemoryExtractionResult(
        updated=True,
        reason="updated",
        memory=memory,
        considered_digest_ids=tuple(source_digest_ids),
    )


def _collect_unique_lines(text: str, *, limit: int) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for piece in (item.strip() for item in text.split("|")):
        if not piece or piece in seen:
            continue
        rows.append(piece)
        seen.add(piece)
        if len(rows) >= limit:
            break
    return rows


def _extract_pending_tasks(digests: list[TurnDigest]) -> list[str]:
    tasks: list[str] = []
    for digest in digests:
        lowered = digest.summary.lower()
        if "todo" in lowered or "pending" in lowered or "next" in lowered:
            tasks.append(digest.summary[:180])
    return tasks[:6]


def _extract_open_questions(digests: list[TurnDigest]) -> list[str]:
    questions: list[str] = []
    for digest in digests:
        if "?" in digest.summary:
            questions.append(digest.summary[:180])
    return questions[:6]


__all__ = ["SessionMemoryExtractionResult", "extract_session_memory"]
