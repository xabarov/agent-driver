"""One-call constructor for a ready-to-use memory provider (memory epic M4).

Long-term memory is fully built but was awkward to turn on: a caller had to know
the store → provider → hook wiring, and the *good* provider
(:class:`FactExtractingMemoryProvider`) needs an aux LLM and extraction config
that is not discoverable. ``build_memory_provider`` collapses that to one call
with sane defaults, so enabling memory is a single obvious line:

    from agent_driver.memory import build_memory_provider
    agent = create_agent(
        provider=llm,
        memory_provider=build_memory_provider(path="mem.sqlite", extractor=llm),
    )

Memory stays opt-in (privacy and cost are the caller's call) — this only makes
the opt-in trivial and makes fact extraction discoverable.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_driver.memory.extraction import FactExtractingMemoryProvider
from agent_driver.memory.provider import MemoryProvider, StoreBackedMemoryProvider
from agent_driver.memory.semantic import EmbeddingMemoryProvider, MemoryEmbedder
from agent_driver.memory.stores import InMemoryMemoryStore, SqliteMemoryStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent_driver.llm.providers import LlmProvider


def build_memory_provider(
    *,
    path: str | None = None,
    extractor: "LlmProvider | None" = None,
    embedder: MemoryEmbedder | None = None,
    model: str | None = None,
    recall_limit: int = 5,
    recall_min_relevance: float = 0.0,
    recall_half_life_seconds: float | None = None,
    defer_sync: bool = True,
) -> MemoryProvider:
    """Build a ready-to-use long-term memory provider with sane defaults.

    - No ``path`` → an in-process, ephemeral store (zero deps; good for tests,
      demos, and single-process sessions).
    - ``path`` → a durable SQLite-backed store that survives process restarts
      (parent directories are created; ``":memory:"`` stays in-process).
    - ``embedder`` (a ``MemoryEmbedder``) → semantic recall by embedding cosine
      similarity, so a paraphrased query still recalls a related memory. Takes
      precedence over ``extractor``.
    - ``extractor`` (an ``LlmProvider``) → LLM fact-extraction into slotted,
      supersedable facts (recommended for recall quality). Without either, whole
      turns are stored raw with keyword recall.

    ``recall_limit`` / ``recall_min_relevance`` / ``recall_half_life_seconds``
    tune how much is recalled, the abstain threshold (interpreted as the minimum
    cosine similarity when an ``embedder`` is given), and temporal decay. Pass
    the result to ``create_agent(memory_provider=...)``.

    ``defer_sync`` (default True) keeps the extraction/embed LLM call off the
    run-completion path — correct on a persistent event loop. A host that runs
    each turn on its OWN short-lived loop (e.g. ``asyncio.run`` per request) must
    pass ``defer_sync=False`` so the sync completes inline before that loop
    closes; otherwise the deferred task is cancelled and nothing is persisted.
    """
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    store = SqliteMemoryStore(path=path) if path else InMemoryMemoryStore()
    if embedder is not None:
        return EmbeddingMemoryProvider(
            store,
            embedder,
            recall_limit=recall_limit,
            recall_min_similarity=recall_min_relevance,
            recall_half_life_seconds=recall_half_life_seconds,
            defer_sync=defer_sync,
        )
    if extractor is not None:
        return FactExtractingMemoryProvider(
            store,
            extractor,
            recall_limit=recall_limit,
            model=model,
            recall_min_relevance=recall_min_relevance,
            recall_half_life_seconds=recall_half_life_seconds,
            defer_sync=defer_sync,
        )
    return StoreBackedMemoryProvider(
        store,
        recall_limit=recall_limit,
        recall_min_relevance=recall_min_relevance,
        recall_half_life_seconds=recall_half_life_seconds,
    )


__all__ = ["build_memory_provider"]
