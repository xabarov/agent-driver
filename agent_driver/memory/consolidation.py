"""Background memory consolidation (epic 031).

Per-turn extraction (021) + slot-supersede + recall hygiene (027) keep recall
*clean*, but never tidy the *store*: cross-slot near-duplicates accumulate,
contradicted facts linger as orphaned records, relative dates ("вчера", "last
week") rot into ambiguity, and raw-fallback turns never get a slot. This module
runs the periodic tidy the two references converge on — but adapted to our shape.

Reference-first, adapted:
  * **hermes background reviewer** forks a full agent with a hard isolation
    checklist (shared session_id for cache warmth BUT persist-disabled so the
    fork never writes into the user's journal). We do NOT need that checklist:
    consolidation here is a single ``aux`` LLM call (epic 034 substrate), not a
    run fork — it touches no run history, no prompt cache, no journal *by
    construction*. Its cost merges into the ledger tagged ``memory_consolidation``.
  * **openclaude autoDream** runs a 4-phase pass (Orient → Gather → Consolidate →
    Prune) with rules «convert relative dates → absolute» and «delete contradicted
    facts». We fold those four phases into one structured emit over the session's
    fact records, and keep openclaude's safety spine: never wipe on failure
    (rollback), a cheap-first gate so a tiny store spends no call.

The pass is conservative: it rewrites the store ONLY when the aux call succeeds
and returns a non-empty, non-growing set. Any failure leaves the store untouched.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.structured import structured_completion
from agent_driver.memory.provider import (
    ConsolidationResult,
    MemoryKind,
    MemoryRecord,
    MemoryStore,
    supersede_by_slot,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent_driver.llm.providers import LlmProvider

logger = logging.getLogger(__name__)

# A pass is worth an LLM call only once a session has accumulated enough to tidy
# OR carries a relative-date marker that will rot. Below this, deterministic
# supersede already keeps recall clean and a call would be waste (openclaude
# cheap-first gate: don't spend on an empty tidy).
_MIN_RECORDS_FOR_PASS = 4
# Backstop cap after consolidation; value-aware eviction (raw_fallback + oldest
# first) replaces the host's blind FIFO so a fresh slotted fact never loses to an
# old raw turn.
_DEFAULT_MAX_RECORDS = 200
_MAX_FACT_SOURCE_CHARS = 260

# Relative-date markers (RU + EN) — their presence alone justifies a pass so the
# date gets absolutized before it becomes uninterpretable.
_RELATIVE_DATE_RE = re.compile(
    r"\b("
    r"вчера|сегодня|завтра|позавчера|послезавтра|"
    r"на\s+прошлой\s+неделе|на\s+этой\s+неделе|на\s+следующей\s+неделе|"
    r"в\s+прошлом\s+месяце|в\s+этом\s+месяце|"
    r"недавно|на\s+днях|"
    r"yesterday|today|tomorrow|last\s+week|this\s+week|next\s+week|"
    r"last\s+month|this\s+month|recently"
    r")\b",
    flags=re.IGNORECASE,
)

_CONSOLIDATION_SYSTEM_PROMPT = (
    "You are a memory curator for a personal assistant. You are given the durable "
    "facts remembered about ONE user, each with a stable numeric id and an optional "
    "slot (a short key naming the subject). Consolidate them into the smallest "
    "faithful set:\n"
    "1. MERGE near-duplicates and facts about the same subject into one clear fact; "
    "reuse the existing slot for that subject.\n"
    "2. DROP a fact that a newer fact contradicts — keep only the current truth "
    "(the higher-id fact is newer).\n"
    "3. Convert relative dates ('вчера', 'last week', 'на прошлой неделе') to "
    "absolute dates using TODAY given below, so they stay interpretable later.\n"
    "4. Give a slot to a slotless fact worth keeping when its subject is clear; "
    "otherwise keep it slotless. Do not invent facts.\n"
    "Preserve every DISTINCT durable fact — merging is not deleting. Never drop a "
    "fact just because it lacks a slot. Write each fact in the language of the "
    "original. Return the consolidated facts via the tool; return the SAME facts "
    "unchanged if nothing can be merged or fixed."
)

# Epic 036 forced-tool channel: a single ``facts`` array. ``sources`` (the ids
# merged into a fact) is optional accounting the model may fill; we tolerate its
# absence.
_CONSOLIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "slot": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text"],
            },
        }
    },
    "required": ["facts"],
}


def _consolidation_candidates(records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Deterministic pre-pass: newest-first, same-slot superseded, exact-text dedup.

    Runs before the LLM so the call sees an already-tidy input (fewer tokens, no
    trivial dups to reason about) and so consolidation degrades to this cheap pass
    when the LLM is skipped or fails.
    """
    superseded = supersede_by_slot(records)
    seen_texts: set[str] = set()
    kept: list[MemoryRecord] = []
    for record in superseded:
        key = record.text.strip().lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)
        kept.append(record)
    return kept


def _should_run_pass(candidates: list[MemoryRecord]) -> bool:
    """Cheap-first gate: enough records to tidy, or a rotting relative date."""
    if len(candidates) >= _MIN_RECORDS_FOR_PASS:
        return True
    return any(_RELATIVE_DATE_RE.search(record.text) for record in candidates)


def _render_candidates(candidates: list[MemoryRecord]) -> str:
    """Number the candidates for the prompt (oldest-first so 'higher id is newer').

    ``candidates`` arrive newest-first; we reverse so the id ordering matches the
    prompt rule "the higher-id fact is newer" and the model can pick the current
    truth on a contradiction.
    """
    lines: list[str] = []
    for fact_id, record in enumerate(reversed(candidates)):
        slot = record.metadata.get("slot")
        slot_tag = f" [slot={slot}]" if isinstance(slot, str) and slot else ""
        source = record.metadata.get("source")
        source_tag = " [raw]" if source == "raw_fallback" else ""
        text = record.text.strip()[:_MAX_FACT_SOURCE_CHARS]
        lines.append(f"[{fact_id}]{slot_tag}{source_tag} {text}")
    return "\n".join(lines)


def _records_from_structured(
    payload: dict[str, Any],
    session_id: str,
    *,
    now_iso: str,
    max_records: int,
) -> list[MemoryRecord] | None:
    """Turn the tool-emitted ``{facts:[...]}`` into fresh consolidated records.

    Returns ``None`` when the payload holds no usable fact (the caller guards a
    wipe). Applies the same slot hygiene as extraction, dedups by text, stamps
    ``source=consolidated`` + a fresh ``created_at``, and caps to ``max_records``
    with value-aware eviction (slotted facts kept over slotless/raw).
    """
    built: list[MemoryRecord] = []
    seen_texts: set[str] = set()
    for item in payload.get("facts") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        clean = text.strip()
        key = clean.lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)
        metadata: dict[str, Any] = {"source": "consolidated", "created_at": now_iso}
        slot = item.get("slot")
        if isinstance(slot, str) and slot.strip():
            normalized = slot.strip().lower()
            if len(normalized) <= 40 and "kebab" not in normalized:
                metadata["slot"] = normalized
        built.append(
            MemoryRecord(
                session_id=session_id,
                text=clean,
                kind=MemoryKind.FACT,
                metadata=metadata,
            )
        )
    if not built:
        return None
    if len(built) > max_records:
        # Value-aware eviction: a slotted fact outranks a slotless one. Stable sort
        # preserves the model's ordering within a class. Log the drop (no silent cap).
        built.sort(key=lambda r: 0 if r.metadata.get("slot") else 1)
        dropped = len(built) - max_records
        logger.info(
            "memory consolidation capped session to %d records (%d dropped)",
            max_records,
            dropped,
        )
        built = built[:max_records]
    return built


async def _llm_consolidate(
    *,
    llm_provider: "LlmProvider",
    candidates: list[MemoryRecord],
    session_id: str,
    model: str | None,
    max_records: int,
    cost_ledger: Any,
    now: datetime | None,
) -> list[MemoryRecord] | str:
    """Run the aux consolidation call over ``candidates``.

    Returns the built records on success, or a string guard reason
    (``aux_call_failed`` / ``empty_result_guarded`` / ``would_grow_guarded``)
    the caller records when it cannot fall back to a deterministic reduction.
    """
    now = now or datetime.now(tz=timezone.utc)
    now_iso = now.isoformat()
    today_line = now.date().isoformat()
    messages = [
        ChatMessage(role=ChatRole.SYSTEM, content=_CONSOLIDATION_SYSTEM_PROMPT),
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"TODAY is {today_line}.\n\nFacts remembered about this user "
                f"(higher id = newer):\n{_render_candidates(candidates)}"
            ),
        ),
    ]
    try:
        emitted = await structured_completion(
            provider=llm_provider,
            messages=messages,
            schema=_CONSOLIDATION_SCHEMA,
            model=model,
            description="Emit the consolidated durable facts for this user.",
            max_retries=1,
            metadata={"purpose": "memory_consolidation"},
            cost_ledger=cost_ledger,
            task="memory_consolidation",
        )
    except Exception:  # noqa: BLE001 - consolidation must never take a run down
        logger.warning(
            "memory consolidation aux call failed; falling back", exc_info=True
        )
        return "aux_call_failed"

    built = _records_from_structured(
        emitted, session_id, now_iso=now_iso, max_records=max_records
    )
    # Safety guards (never wipe, never grow beyond the input) — mirror hermes
    # «archive is the max destructive action»: when in doubt, do not trust the emit.
    if built is None:
        return "empty_result_guarded"
    if len(built) > len(candidates):
        return "would_grow_guarded"
    return built


async def consolidate_session(
    *,
    store: MemoryStore,
    llm_provider: "LlmProvider",
    session_id: str,
    model: str | None = None,
    max_records: int = _DEFAULT_MAX_RECORDS,
    cost_ledger: Any = None,
    now: datetime | None = None,
) -> ConsolidationResult:
    """Fold a session's fact store into a compacter, contradiction-free set.

    Conservative by design (openclaude rollback spirit): the store is rewritten
    ONLY when the aux call returns a non-empty, non-growing set. On a skipped gate,
    an empty/failed emit, a would-grow result, or a store without ``replace_session``
    the store is left exactly as-is and the reason is recorded. All counts are
    raw-free.
    """
    records = list(store.list_for_session(session_id))
    before_count = len(records)
    result = ConsolidationResult(session_id=session_id, before_count=before_count)

    if not hasattr(store, "replace_session"):
        result.reason = "store_cannot_rewrite"
        return result

    # Deterministic pre-pass: same-slot supersede + exact-text dedup already
    # collapse the trivial cases without spending a call. Its output is a valid
    # consolidation on its own and the LLM's floor when the store can rewrite.
    candidates = _consolidation_candidates(records)
    deterministic_reduced = len(candidates) < before_count

    final = candidates
    used_llm = False
    gate_open = _should_run_pass(candidates)
    if gate_open:
        outcome = await _llm_consolidate(
            llm_provider=llm_provider,
            candidates=candidates,
            session_id=session_id,
            model=model,
            max_records=max_records,
            cost_ledger=cost_ledger,
            now=now,
        )
        if isinstance(outcome, list):
            final = outcome
            used_llm = True
        elif not deterministic_reduced:
            # Guarded emit (empty/would-grow) or aux failure, and nothing the
            # deterministic pass shrank: leave the store exactly as-is and report
            # why (openclaude rollback spirit).
            result.reason = str(outcome)
            return result

    if not used_llm and not deterministic_reduced:
        # Below the LLM gate and nothing to collapse: already as tidy as this pass
        # makes it.
        result.reason = "below_gate" if not gate_open else "no_change"
        return result

    try:
        persisted = store.replace_session(session_id, final)
    except Exception:  # noqa: BLE001
        logger.warning("memory consolidation replace_session failed", exc_info=True)
        result.reason = "replace_failed"
        return result

    result.after_count = len(persisted)
    result.merged = max(0, before_count - result.after_count)
    result.applied = True
    result.reason = "applied" if used_llm else "deterministic"
    logger.info(
        "memory consolidation applied for session (%s): %d -> %d records",
        result.reason,
        before_count,
        result.after_count,
    )
    return result


__all__ = ["consolidate_session"]
