"""Tests for the store-backed long-term memory provider."""

from __future__ import annotations

import pytest

from agent_driver.memory import (
    InMemoryMemoryStore,
    MemoryRecord,
    MemoryTurn,
    RecallQuery,
    StoreBackedMemoryProvider,
    apply_recall,
    match_query,
)


def _provider() -> StoreBackedMemoryProvider:
    return StoreBackedMemoryProvider(InMemoryMemoryStore(), recall_limit=5)


@pytest.mark.asyncio
async def test_sync_turn_stores_user_and_assistant() -> None:
    """A finished turn persists both user and assistant text."""
    provider = _provider()
    await provider.sync_turn(
        MemoryTurn(
            session_id="s1",
            run_id="r1",
            user_text="Where do I deploy?",
            assistant_text="Deploy to the eu-west cluster.",
        )
    )
    result = await provider.prefetch(RecallQuery(session_id="s1"))
    texts = [record.text for record in result.records]
    assert "Deploy to the eu-west cluster." in texts
    assert "Where do I deploy?" in texts
    # Newest-first: assistant text was appended last.
    assert result.records[0].text == "Deploy to the eu-west cluster."
    assert result.records[0].metadata["role"] == "assistant"
    assert result.records[0].metadata["run_id"] == "r1"


@pytest.mark.asyncio
async def test_prefetch_keyword_recall_filters() -> None:
    """A keyword query returns only matching records, newest-first."""
    provider = _provider()
    await provider.sync_turn(
        MemoryTurn(session_id="s1", assistant_text="The API key lives in Vault.")
    )
    await provider.sync_turn(
        MemoryTurn(session_id="s1", assistant_text="Lunch is at noon.")
    )
    result = await provider.prefetch(RecallQuery(session_id="s1", query="vault key"))
    assert len(result.records) == 1
    assert "Vault" in result.records[0].text


@pytest.mark.asyncio
async def test_prefetch_respects_limit() -> None:
    """Recall caps results to the query limit."""
    provider = _provider()
    for i in range(10):
        await provider.sync_turn(
            MemoryTurn(session_id="s1", assistant_text=f"fact {i}")
        )
    result = await provider.prefetch(RecallQuery(session_id="s1", limit=3))
    assert len(result.records) == 3
    # Newest-first: last appended is "fact 9".
    assert result.records[0].text == "fact 9"


@pytest.mark.asyncio
async def test_sessions_are_isolated() -> None:
    """Recall never crosses session boundaries."""
    provider = _provider()
    await provider.sync_turn(MemoryTurn(session_id="s1", assistant_text="secret one"))
    await provider.sync_turn(MemoryTurn(session_id="s2", assistant_text="secret two"))
    result = await provider.prefetch(RecallQuery(session_id="s2"))
    texts = [record.text for record in result.records]
    assert texts == ["secret two"]


@pytest.mark.asyncio
async def test_empty_and_whitespace_text_not_stored() -> None:
    """Blank user/assistant text is skipped."""
    provider = _provider()
    await provider.sync_turn(
        MemoryTurn(session_id="s1", user_text="   ", assistant_text="")
    )
    result = await provider.prefetch(RecallQuery(session_id="s1"))
    assert result.records == []


@pytest.mark.asyncio
async def test_remember_flags_disable_roles() -> None:
    """Disabling a role prevents persisting that side of the turn."""
    provider = StoreBackedMemoryProvider(InMemoryMemoryStore(), remember_user=False)
    await provider.sync_turn(
        MemoryTurn(session_id="s1", user_text="hi", assistant_text="hello")
    )
    result = await provider.prefetch(RecallQuery(session_id="s1"))
    assert [r.metadata["role"] for r in result.records] == ["assistant"]


def test_match_query_any_term() -> None:
    """A record matches when it contains any query term, case-insensitively."""
    assert match_query("The Vault holds keys", "vault") is True
    assert match_query("The Vault holds keys", "secret vault") is True
    assert match_query("unrelated text", "vault") is False
    assert match_query("anything", "") is True


def test_apply_recall_pure_helper() -> None:
    """apply_recall filters and caps newest-first records."""
    records = [
        MemoryRecord(session_id="s", text="alpha vault", seq=3),
        MemoryRecord(session_id="s", text="beta", seq=2),
        MemoryRecord(session_id="s", text="gamma vault", seq=1),
    ]
    out = apply_recall(records, "vault", limit=1)
    assert [r.text for r in out] == ["alpha vault"]
    out_all = apply_recall(records, None, limit=2)
    assert len(out_all) == 2
