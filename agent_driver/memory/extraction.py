"""Fact-extracting memory provider (epic 021 phase B).

Instead of persisting raw turn texts, this provider asks a small LLM to distill
each finished turn into zero or more *durable facts* — stable preferences,
identities, standing decisions — and stores those as ``MemoryKind.FACT``
records. Each fact carries a ``slot``: a short stable key naming its subject.
When a later turn produces a fact with the same slot (the user changed their
preference), recall keeps only the newest record per slot — append-only
supersede, no store mutation API required.

Fail-open discipline: memory must never take a run down. Any extraction
failure (provider error, unparseable output) falls back to the raw-turn
behavior of :class:`StoreBackedMemoryProvider` (configurable) or skips
persistence entirely — it never raises into the run lifecycle.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.memory.provider import (
    MemoryKind,
    MemoryProvider,
    MemoryRecord,
    MemoryStore,
    MemoryTurn,
    RecallQuery,
    RecallResult,
    apply_recall,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent_driver.llm.providers import LlmProvider

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM_PROMPT = (
    "You distill chat turns into durable long-term memory for a personal "
    "assistant. Extract ONLY facts worth remembering across future sessions: "
    "stable user preferences, identities, standing decisions, recurring "
    "interests, explicit 'remember this' requests. Do NOT extract one-off "
    "questions, retrieved document content, or anything episodic.\n"
    "Answer with a JSON array (no prose, no code fences). Each element: "
    '{"text": "<the fact, one sentence, in the language of the conversation>", '
    '"slot": "<short-stable-kebab-key-naming-the-subject>"}. '
    "Use the SAME slot when a fact updates or replaces an earlier fact about "
    "the same subject (e.g. team-codeword, preferred-answer-format). "
    "Return [] when nothing is worth keeping."
)

_MAX_SOURCE_CHARS = 4000


def parse_extracted_facts(content: str, *, max_facts: int) -> list[dict[str, str]]:
    """Parse the extraction completion into ``[{"text", "slot"}, ...]``.

    Tolerates code fences and surrounding prose (grabs the first JSON array).
    Raises ``ValueError`` when no valid array of fact objects can be read.
    """
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", stripped, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else stripped
    if not candidate.startswith("["):
        embedded = re.search(r"\[.*\]", candidate, flags=re.DOTALL)
        if embedded is None:
            raise ValueError("no JSON array in extraction output")
        candidate = embedded.group(0)
    payload = json.loads(candidate)
    if not isinstance(payload, list):
        raise ValueError("extraction output is not a list")
    facts: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        slot = item.get("slot")
        fact: dict[str, str] = {"text": text.strip()}
        if isinstance(slot, str) and slot.strip():
            normalized_slot = slot.strip().lower()
            # Live artifact: weak models occasionally echo the schema placeholder
            # («short-stable-kebab-key-naming-the-subject») as the slot. Real slots
            # are short keys; drop implausible ones so a bogus slot can't shadow
            # (or escape) supersede for the actual subject.
            if len(normalized_slot) <= 40 and "kebab" not in normalized_slot:
                fact["slot"] = normalized_slot
        if any(existing["text"] == fact["text"] for existing in facts):
            continue
        facts.append(fact)
        if len(facts) >= max_facts:
            break
    return facts


def supersede_by_slot(records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Keep only the newest record per ``slot`` (input is newest-first).

    Records without a slot (raw turns, legacy entries) pass through untouched —
    supersede only applies where a stable subject key exists.
    """
    seen_slots: set[str] = set()
    kept: list[MemoryRecord] = []
    for record in records:
        slot = record.metadata.get("slot")
        if isinstance(slot, str) and slot:
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
        kept.append(record)
    return kept


class FactExtractingMemoryProvider(MemoryProvider):
    """LLM-distilled facts with append-only slot supersede at recall time."""

    def __init__(
        self,
        store: MemoryStore,
        llm_provider: "LlmProvider",
        *,
        recall_limit: int = 5,
        model: str | None = None,
        max_facts_per_turn: int = 5,
        fallback_raw: bool = True,
        recall_max_chars: int = 2000,
    ) -> None:
        self._store = store
        self._llm_provider = llm_provider
        self._recall_limit = recall_limit
        self._model = model
        self._max_facts_per_turn = max_facts_per_turn
        self._fallback_raw = fallback_raw
        # Read by MemoryLifecycleHook to bound the injected recall block.
        self.recall_max_chars = recall_max_chars

    @property
    def store(self) -> MemoryStore:
        """Return the backing store."""
        return self._store

    async def prefetch(self, query: RecallQuery) -> RecallResult:
        """Newest matching records with older same-slot facts superseded."""
        candidates = supersede_by_slot(self._store.list_for_session(query.session_id))
        records = apply_recall(candidates, query.query, query.limit)
        return RecallResult(session_id=query.session_id, records=records)

    async def sync_turn(self, turn: MemoryTurn) -> None:
        """Distill the turn into FACT records; fail open on any error."""
        try:
            facts = await self._extract_facts(turn)
        except Exception:  # noqa: BLE001 - deliberate fail-open boundary
            logger.warning("memory fact extraction failed; falling back", exc_info=True)
            if self._fallback_raw:
                await self._sync_raw_turn(turn)
            return
        for fact in facts:
            metadata: dict[str, Any] = {"source": "fact_extraction", **turn.metadata}
            if "slot" in fact:
                metadata["slot"] = fact["slot"]
            if turn.run_id is not None:
                metadata.setdefault("run_id", turn.run_id)
            self._store.append(
                MemoryRecord(
                    session_id=turn.session_id,
                    text=fact["text"],
                    kind=MemoryKind.FACT,
                    metadata=metadata,
                )
            )

    async def _extract_facts(self, turn: MemoryTurn) -> list[dict[str, str]]:
        parts = []
        if turn.user_text:
            parts.append(f"User: {turn.user_text[:_MAX_SOURCE_CHARS]}")
        if turn.assistant_text:
            parts.append(f"Assistant: {turn.assistant_text[:_MAX_SOURCE_CHARS]}")
        if not parts:
            return []
        request = LlmRequest(
            messages=[
                ChatMessage(role=ChatRole.SYSTEM, content=_EXTRACTION_SYSTEM_PROMPT),
                ChatMessage(role=ChatRole.USER, content="\n".join(parts)),
            ],
            model=self._model,
            temperature=0.0,
            max_tokens=512,
            metadata={"purpose": "memory_fact_extraction"},
        )
        response = await self._llm_provider.complete(request)
        return parse_extracted_facts(
            response.message.content or "", max_facts=self._max_facts_per_turn
        )

    async def _sync_raw_turn(self, turn: MemoryTurn) -> None:
        """Raw-turn fallback mirroring :class:`StoreBackedMemoryProvider`."""
        for role, text in (
            ("user", turn.user_text),
            ("assistant", turn.assistant_text),
        ):
            if not text or not text.strip():
                continue
            metadata: dict[str, Any] = {
                "role": role,
                "source": "raw_fallback",
                **turn.metadata,
            }
            if turn.run_id is not None:
                metadata.setdefault("run_id", turn.run_id)
            self._store.append(
                MemoryRecord(
                    session_id=turn.session_id,
                    text=text.strip(),
                    kind=MemoryKind.TURN,
                    metadata=metadata,
                )
            )


__all__ = [
    "FactExtractingMemoryProvider",
    "parse_extracted_facts",
    "supersede_by_slot",
]
