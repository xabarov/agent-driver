"""Memory epic M4 — one-call `build_memory_provider` opt-in helper.

Collapses the store → provider wiring to a single obvious line and makes fact
extraction (the quality path) discoverable, while keeping memory opt-in.
"""

from __future__ import annotations

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import (
    FactExtractingMemoryProvider,
    InMemoryMemoryStore,
    SqliteMemoryStore,
    StoreBackedMemoryProvider,
    build_memory_provider,
)
from agent_driver.sdk import ToolSet, create_agent


def test_default_is_in_process_store_backed() -> None:
    provider = build_memory_provider()
    assert isinstance(provider, StoreBackedMemoryProvider)
    assert isinstance(provider.store, InMemoryMemoryStore)


def test_path_yields_durable_sqlite_and_creates_parent(tmp_path) -> None:
    db = tmp_path / "nested" / "mem.sqlite"
    provider = build_memory_provider(path=str(db))
    assert isinstance(provider.store, SqliteMemoryStore)
    assert db.parent.is_dir()


def test_memory_sentinel_path_stays_in_process(tmp_path) -> None:
    provider = build_memory_provider(path=":memory:")
    assert isinstance(provider.store, SqliteMemoryStore)


def test_extractor_yields_fact_extracting_provider() -> None:
    provider = build_memory_provider(extractor=FakeProvider())
    assert isinstance(provider, FactExtractingMemoryProvider)


def test_recall_knobs_are_threaded() -> None:
    provider = build_memory_provider(recall_min_relevance=0.4)
    assert provider._recall_min_relevance == 0.4


@pytest.mark.asyncio
async def test_end_to_end_opt_in_recalls_across_runs() -> None:
    """The documented one-liner actually gives cross-session recall."""
    provider = build_memory_provider()
    agent = create_agent(
        provider=FakeProvider(name="cap", response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=provider,
    )
    session = agent.session("user-1")
    await session.send("Remember: the deploy target is eu-west-3.", run_id="r1")
    stored = provider.store.list_for_session("user-1")
    assert any("eu-west-3" in r.text for r in stored)
