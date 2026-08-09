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
from agent_driver.llm.structured import structured_completion
from agent_driver.memory.consolidation import consolidate_session
from agent_driver.memory.guidance import MEMORY_WRITE_GATING
from agent_driver.memory.provider import (
    ConsolidationResult,
    MemoryKind,
    MemoryProvider,
    MemoryRecord,
    MemoryStore,
    MemoryTurn,
    RecallQuery,
    RecallResult,
    apply_recall,
    sanitize_memory_text,
    supersede_by_slot,
    sync_raw_turn,
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
    # Epic 031 C: a user correcting the assistant is a FIRST-CLASS durable signal
    # (hermes: «Frustration is a first-class skill signal»). Distil it into a
    # standing preference so the next session starts already knowing.
    "If the user CORRECTS the assistant — its format, tone, verbosity, a term, or "
    "a fact ('короче', 'слишком длинно', 'не пиши так', 'просто дай ответ', 'не "
    "'X' а 'Y'', 'запомни, что…') — extract that correction as a durable fact with "
    "a stable slot naming what it governs (e.g. answer-format, preferred-term-"
    "<subject>). These outrank ordinary preferences.\n"
    "Answer with a JSON array (no prose, no code fences). Each element: "
    '{"text": "<the fact, one sentence, in the language of the conversation>", '
    '"slot": "<short-stable-kebab-key-naming-the-subject>"}. '
    "Use the SAME slot when a fact updates or replaces an earlier fact about "
    "the same subject (e.g. team-codeword, preferred-answer-format). "
    # Epic 039: facts recalled later must read naturally in the user's language.
    "Write each fact text in the language of the conversation (do not translate). "
    "Return [] when nothing is worth keeping.\n"
    # Epic M2: the shared write-gating discipline (same source of truth as the
    # `remember` tool) so the extractor and the model apply identical exclusions.
    + MEMORY_WRITE_GATING
)

_MAX_SOURCE_CHARS = 4000

# Epic 036: schema for the forced-tool extraction channel. A single ``facts``
# array of {text, slot}; the reliable tool-call path replaces free-JSON parsing.
_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "slot": {"type": "string"},
                },
                "required": ["text"],
            },
        }
    },
    "required": ["facts"],
}


def _facts_from_structured(payload: dict, *, max_facts: int) -> list[dict[str, str]]:
    """Normalize the tool-emitted ``{facts: [...]}`` into ``[{text, slot?}]``.

    Applies the same slot-hygiene and dedup as the legacy free-JSON parser so
    supersede behaviour is identical (schema placeholder rejected, dup texts
    dropped, per-turn cap enforced).
    """
    facts: list[dict[str, str]] = []
    for item in payload.get("facts") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        fact: dict[str, str] = {"text": text.strip()}
        slot = item.get("slot")
        if isinstance(slot, str) and slot.strip():
            normalized_slot = slot.strip().lower()
            if len(normalized_slot) <= 40 and "kebab" not in normalized_slot:
                fact["slot"] = normalized_slot
        if any(existing["text"] == fact["text"] for existing in facts):
            continue
        facts.append(fact)
        if len(facts) >= max_facts:
            break
    return facts


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


class FactExtractingMemoryProvider(MemoryProvider):
    """LLM-distilled facts with append-only slot supersede at recall time."""

    # sync_turn makes an LLM call; the runtime memory hook defers it off the
    # run-completion critical path (flushed at shutdown / before next recall).
    defer_sync = True

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
        recall_min_relevance: float = 0.0,
        recall_half_life_seconds: float | None = None,
        consolidation_max_records: int = 200,
    ) -> None:
        self._store = store
        self._llm_provider = llm_provider
        self._recall_limit = recall_limit
        self._model = model
        self._max_facts_per_turn = max_facts_per_turn
        self._fallback_raw = fallback_raw
        # Read by MemoryLifecycleHook to bound the injected recall block.
        self.recall_max_chars = recall_max_chars
        # Epic 027: abstain threshold + temporal decay for recall ranking.
        self._recall_min_relevance = recall_min_relevance
        self._recall_half_life_seconds = recall_half_life_seconds
        # Epic 031: backstop cap for the consolidation rewrite.
        self._consolidation_max_records = consolidation_max_records

    @property
    def store(self) -> MemoryStore:
        """Return the backing store."""
        return self._store

    async def prefetch(self, query: RecallQuery) -> RecallResult:
        """Newest matching records with older same-slot facts superseded."""
        candidates = supersede_by_slot(self._store.list_for_session(query.session_id))
        records = apply_recall(
            candidates,
            query.query,
            query.limit,
            min_relevance=self._recall_min_relevance,
            half_life_seconds=self._recall_half_life_seconds,
        )
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
            source = sanitize_memory_text(turn.user_text)
            if source:
                parts.append(f"User: {source[:_MAX_SOURCE_CHARS]}")
        if turn.assistant_text:
            source = sanitize_memory_text(turn.assistant_text)
            if source:
                parts.append(f"Assistant: {source[:_MAX_SOURCE_CHARS]}")
        if not parts:
            return []
        messages = [
            ChatMessage(role=ChatRole.SYSTEM, content=_EXTRACTION_SYSTEM_PROMPT),
            ChatMessage(role=ChatRole.USER, content="\n".join(parts)),
        ]
        # Epic 036: distilled facts come back over the forced tool-call channel,
        # not free JSON — the dd9a5ee «prose instead of a JSON array» flake is
        # impossible by construction (the arguments are validated at the tool
        # layer, and an invalid emit is a tool error the model self-repairs).
        raw = await structured_completion(
            provider=self._llm_provider,
            messages=messages,
            schema=_EXTRACTION_SCHEMA,
            model=self._model,
            description="Emit durable long-term facts extracted from the turn.",
            max_retries=1,
            metadata={"purpose": "memory_fact_extraction"},
        )
        return _facts_from_structured(raw, max_facts=self._max_facts_per_turn)

    async def consolidate(
        self, session_id: str, *, cost_ledger: Any = None
    ) -> "ConsolidationResult | None":
        """Fold the session's fact store into a compacter, consistent set.

        Delegates to :func:`consolidate_session` — a single cache-safe aux call
        over this session's records, conservative on failure (store untouched).
        """
        return await consolidate_session(
            store=self._store,
            llm_provider=self._llm_provider,
            session_id=session_id,
            model=self._model,
            max_records=self._consolidation_max_records,
            cost_ledger=cost_ledger,
        )

    async def _sync_raw_turn(self, turn: MemoryTurn) -> None:
        """Raw-turn fallback mirroring :class:`StoreBackedMemoryProvider`."""
        sync_raw_turn(self._store, turn, extra_metadata={"source": "raw_fallback"})


__all__ = [
    "FactExtractingMemoryProvider",
    "parse_extracted_facts",
    "supersede_by_slot",
]
