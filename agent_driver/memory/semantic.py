"""Semantic (embedding-backed) long-term memory recall (memory epic M5).

Keyword recall degrades on paraphrase: "where do we ship?" does not match a
stored "the deploy target is eu-west-3" because they share no tokens. This
provider ranks recall by embedding cosine similarity instead, so a semantically
related memory surfaces even with no lexical overlap.

It honors the existing storage split (the ``provider.py`` docstring anticipates
"an embedding-backed store can implement the same protocol later"): the semantic
logic lives in the *provider*, not a new store type. Vectors ride in
``MemoryRecord.metadata["embedding"]`` on the ordinary in-memory/SQLite store, so
no runtime wiring changes — the lifecycle hook calls ``prefetch``/``sync_turn``
exactly as before. The embedder is caller-supplied (a small async protocol), so
the SDK forces no embedding dependency; a ``FakeEmbedder`` covers tests.

Cosine is computed in pure Python (no numpy): sessions are small and vectors
modest, and it keeps the dependency surface at zero. A production deployment with
large stores would back this with a real vector index behind the same provider.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol

from agent_driver.memory.provider import (
    MemoryKind,
    MemoryProvider,
    MemoryRecord,
    MemoryStore,
    MemoryTurn,
    RecallQuery,
    RecallResult,
    _decay_factor,
    apply_recall,
    sanitize_memory_text,
    supersede_by_slot,
)

logger = logging.getLogger(__name__)

_EMBEDDING_KEY = "embedding"


class MemoryEmbedder(Protocol):
    """Turns texts into vectors for semantic memory recall.

    A batched async call so one round-trip embeds many texts. Caller-supplied so
    the SDK forces no embedding dependency (OpenAI embeddings, a local model, or
    a test double all satisfy this).
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""
        ...


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 on any degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _record_vector(record: MemoryRecord) -> list[float] | None:
    vec = record.metadata.get(_EMBEDDING_KEY)
    if isinstance(vec, list) and vec and all(isinstance(x, (int, float)) for x in vec):
        return [float(x) for x in vec]
    return None


class EmbeddingMemoryProvider(MemoryProvider):
    """Semantic recall over a :class:`MemoryStore`, vectors kept in metadata.

    Writes embed the turn's text and persist the vector alongside it; recall
    embeds the query and ranks candidates by cosine similarity × temporal decay,
    with an abstain gate. Records stored by another path (e.g. explicit
    ``remember`` writes, which append directly) carry no vector and are embedded
    lazily on read. Fails open to keyword recall if the embedder errors.
    """

    # sync_turn makes an embed call; the hook defers it off the completion path.
    defer_sync = True

    def __init__(
        self,
        store: MemoryStore,
        embedder: MemoryEmbedder,
        *,
        recall_limit: int = 5,
        remember_user: bool = True,
        remember_assistant: bool = True,
        recall_max_chars: int = 2000,
        recall_min_similarity: float = 0.0,
        recall_half_life_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._recall_limit = recall_limit
        self._remember_user = remember_user
        self._remember_assistant = remember_assistant
        # Read by MemoryLifecycleHook to bound the injected recall block.
        self.recall_max_chars = recall_max_chars
        self._recall_min_similarity = recall_min_similarity
        self._recall_half_life_seconds = recall_half_life_seconds

    @property
    def store(self) -> MemoryStore:
        """Return the backing store."""
        return self._store

    async def prefetch(self, query: RecallQuery) -> RecallResult:
        """Return semantically closest records (falls back to keyword on error)."""
        candidates = supersede_by_slot(self._store.list_for_session(query.session_id))
        if not query.query or not query.query.strip():
            # No query text → recency mode, exactly like the keyword provider.
            return RecallResult(
                session_id=query.session_id, records=candidates[: query.limit]
            )
        try:
            ranked = await self._semantic_rank(query.query, candidates, query.limit)
        except Exception:  # noqa: BLE001 - recall must never crash a run
            logger.warning("semantic recall failed; falling back to keyword", exc_info=True)
            ranked = apply_recall(
                candidates,
                query.query,
                query.limit,
                half_life_seconds=self._recall_half_life_seconds,
            )
        return RecallResult(session_id=query.session_id, records=ranked)

    async def _semantic_rank(
        self, query_text: str, candidates: list[MemoryRecord], limit: int
    ) -> list[MemoryRecord]:
        if not candidates:
            return []
        query_vec = (await self._embedder.embed([query_text]))[0]
        vectors: list[list[float] | None] = [_record_vector(c) for c in candidates]
        # Lazily embed candidates that were stored without a vector (e.g. explicit
        # `remember` facts appended directly to the store) — one batched call.
        missing = [i for i, v in enumerate(vectors) if v is None]
        if missing:
            embedded = await self._embedder.embed([candidates[i].text for i in missing])
            for slot, vec in zip(missing, embedded):
                vectors[slot] = vec
        scored: list[tuple[float, int, MemoryRecord]] = []
        for index, (record, vec) in enumerate(zip(candidates, vectors)):
            if vec is None:
                continue
            sim = cosine_similarity(query_vec, vec)
            if sim <= 0.0 or sim < self._recall_min_similarity:
                continue
            weight = sim * _decay_factor(
                record, half_life_seconds=self._recall_half_life_seconds, now=None
            )
            scored.append((weight, index, record))
        scored.sort(key=lambda item: (-item[0], item[1]))  # score desc, then newest
        return [record for _w, _i, record in scored[:limit]]

    async def sync_turn(self, turn: MemoryTurn) -> None:
        """Persist the turn's text with an embedding; fail open to raw storage."""
        from datetime import datetime, timezone

        pairs = [
            (role, text)
            for role, text, enabled in (
                ("user", turn.user_text, self._remember_user),
                ("assistant", turn.assistant_text, self._remember_assistant),
            )
            if enabled and text and text.strip()
        ]
        cleaned = [(role, sanitize_memory_text(text)) for role, text in pairs]
        cleaned = [(role, text) for role, text in cleaned if text]
        if not cleaned:
            return
        stamped = datetime.now(tz=timezone.utc).isoformat()
        try:
            vectors = await self._embedder.embed([text for _role, text in cleaned])
        except Exception:  # noqa: BLE001 - a failed embed must not lose the turn
            logger.warning("embedding a turn failed; storing without vector", exc_info=True)
            vectors = [None] * len(cleaned)  # type: ignore[list-item]
        for (role, text), vec in zip(cleaned, vectors):
            metadata: dict[str, Any] = {"role": role, "created_at": stamped}
            if turn.run_id is not None:
                metadata["run_id"] = turn.run_id
            if vec is not None:
                metadata[_EMBEDDING_KEY] = list(vec)
            self._store.append(
                MemoryRecord(
                    session_id=turn.session_id,
                    text=text.strip(),
                    kind=MemoryKind.TURN,
                    metadata=metadata,
                )
            )


__all__ = ["EmbeddingMemoryProvider", "MemoryEmbedder", "cosine_similarity"]
