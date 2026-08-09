"""Memory epic M5 — semantic (embedding) recall behind the existing protocol.

A synonym-aware FakeEmbedder maps concept groups to shared dimensions, so a
paraphrased query with NO shared tokens still recalls a related memory — the
thing keyword recall cannot do.
"""

from __future__ import annotations

import pytest

from agent_driver.memory import (
    EmbeddingMemoryProvider,
    InMemoryMemoryStore,
    StoreBackedMemoryProvider,
    build_memory_provider,
    cosine_similarity,
)
from agent_driver.memory.provider import MemoryTurn, RecallQuery

# Concept groups → a shared vector dimension. Words in the same group embed to
# the same axis, so synonyms are cosine-close despite zero lexical overlap.
_GROUPS = [
    {"deploy", "ship", "release", "rollout", "deployment"},
    {"budget", "cost", "price", "spend", "money"},
    {"timezone", "tz", "time"},
]


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            tokens = {t.strip(".,?!").lower() for t in text.split()}
            vectors.append([1.0 if g & tokens else 0.0 for g in _GROUPS])
        return vectors


# --- cosine unit ---------------------------------------------------------------


def test_cosine_similarity_basics() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], [1.0]) == 0.0  # degenerate
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero norm


# --- semantic recall beats keyword on paraphrase -------------------------------


@pytest.mark.asyncio
async def test_paraphrase_recall_where_keyword_fails() -> None:
    store = InMemoryMemoryStore()
    provider = EmbeddingMemoryProvider(store, _FakeEmbedder())
    await provider.sync_turn(
        MemoryTurn(session_id="s", assistant_text="The deploy target is eu-west-3.")
    )
    # Query shares NO tokens with the stored fact ("ship" vs "deploy").
    result = await provider.prefetch(RecallQuery(session_id="s", query="where do we ship?"))
    assert any("eu-west-3" in r.text for r in result.records)

    # Keyword recall over the same store misses it (no shared meaningful term).
    keyword = StoreBackedMemoryProvider(store)
    kw = await keyword.prefetch(RecallQuery(session_id="s", query="where do we ship?"))
    assert not any("eu-west-3" in r.text for r in kw.records)


@pytest.mark.asyncio
async def test_ranks_the_closer_concept_first() -> None:
    store = InMemoryMemoryStore()
    provider = EmbeddingMemoryProvider(store, _FakeEmbedder())
    await provider.sync_turn(MemoryTurn(session_id="s", assistant_text="Deploy to eu-west-3."))
    await provider.sync_turn(MemoryTurn(session_id="s", assistant_text="The budget is 14M."))
    result = await provider.prefetch(
        RecallQuery(session_id="s", query="how much does it cost?", limit=1)
    )
    assert len(result.records) == 1
    assert "budget" in result.records[0].text.lower()


@pytest.mark.asyncio
async def test_unrelated_query_abstains() -> None:
    store = InMemoryMemoryStore()
    provider = EmbeddingMemoryProvider(store, _FakeEmbedder())
    await provider.sync_turn(MemoryTurn(session_id="s", assistant_text="Deploy to eu-west-3."))
    result = await provider.prefetch(
        RecallQuery(session_id="s", query="what is the weather like today?")
    )
    assert result.records == []  # zero similarity → nothing recalled


@pytest.mark.asyncio
async def test_stores_vector_in_metadata() -> None:
    store = InMemoryMemoryStore()
    provider = EmbeddingMemoryProvider(store, _FakeEmbedder())
    await provider.sync_turn(MemoryTurn(session_id="s", assistant_text="Deploy now."))
    rec = store.list_for_session("s")[0]
    assert rec.metadata["embedding"] == [1.0, 0.0, 0.0]
    assert rec.metadata["created_at"]


@pytest.mark.asyncio
async def test_lazily_embeds_records_without_a_vector() -> None:
    # Explicit `remember` facts append directly (no embedding) — recall must
    # still find them by embedding on read.
    store = InMemoryMemoryStore()
    from agent_driver.memory.provider import sync_explicit_writes

    sync_explicit_writes(store, "s", [{"text": "Release plan is monthly.", "slot": None}])
    provider = EmbeddingMemoryProvider(store, _FakeEmbedder())
    result = await provider.prefetch(RecallQuery(session_id="s", query="when do we ship?"))
    assert any("Release plan" in r.text for r in result.records)


@pytest.mark.asyncio
async def test_fails_open_to_keyword_on_embedder_error() -> None:
    class _BrokenEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding service down")

    store = InMemoryMemoryStore()
    # Store a record with a shared keyword so the keyword fallback can find it.
    StoreBackedMemoryProvider(store)  # (not used; just for clarity)
    await StoreBackedMemoryProvider(store).sync_turn(
        MemoryTurn(session_id="s", assistant_text="The deploy target is eu-west-3.")
    )
    provider = EmbeddingMemoryProvider(store, _BrokenEmbedder())
    # Recall must not raise; falls back to keyword and finds the shared term.
    result = await provider.prefetch(RecallQuery(session_id="s", query="deploy target"))
    assert any("eu-west-3" in r.text for r in result.records)


def test_build_memory_provider_embedder_precedence() -> None:
    provider = build_memory_provider(embedder=_FakeEmbedder())
    assert isinstance(provider, EmbeddingMemoryProvider)
