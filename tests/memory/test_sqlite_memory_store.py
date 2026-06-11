"""Tests for the durable SQLite memory store."""

from __future__ import annotations

import pytest

from agent_driver.memory import (
    MemoryKind,
    MemoryRecord,
    MemoryTurn,
    RecallQuery,
    SqliteMemoryStore,
    StoreBackedMemoryProvider,
)


def test_append_assigns_monotonic_seq(tmp_path) -> None:
    """The DB assigns increasing seq values on append."""
    store = SqliteMemoryStore(path=str(tmp_path / "mem.sqlite3"))
    first = store.append(MemoryRecord(session_id="s", text="one"))
    second = store.append(MemoryRecord(session_id="s", text="two"))
    assert first.seq >= 1
    assert second.seq > first.seq
    store.close()


def test_list_newest_first_and_limit(tmp_path) -> None:
    """Records come back newest-first and respect the limit."""
    store = SqliteMemoryStore(path=str(tmp_path / "mem.sqlite3"))
    for i in range(5):
        store.append(MemoryRecord(session_id="s", text=f"r{i}"))
    rows = store.list_for_session("s", limit=2)
    assert [r.text for r in rows] == ["r4", "r3"]
    store.close()


def test_metadata_and_kind_round_trip(tmp_path) -> None:
    """Kind and JSON metadata survive a persistence round trip."""
    store = SqliteMemoryStore(path=str(tmp_path / "mem.sqlite3"))
    store.append(
        MemoryRecord(
            session_id="s",
            text="hi",
            kind=MemoryKind.FACT,
            metadata={"role": "assistant", "run_id": "r1"},
        )
    )
    row = store.list_for_session("s")[0]
    assert row.kind is MemoryKind.FACT
    assert row.metadata == {"role": "assistant", "run_id": "r1"}
    store.close()


def test_clear_and_isolation(tmp_path) -> None:
    """Clearing one session leaves others intact."""
    store = SqliteMemoryStore(path=str(tmp_path / "mem.sqlite3"))
    store.append(MemoryRecord(session_id="s1", text="a"))
    store.append(MemoryRecord(session_id="s2", text="b"))
    store.clear("s1")
    assert store.list_for_session("s1") == []
    assert [r.text for r in store.list_for_session("s2")] == ["b"]
    store.close()


def test_durable_across_reopen(tmp_path) -> None:
    """Records persist after closing and reopening the store file."""
    path = str(tmp_path / "mem.sqlite3")
    store = SqliteMemoryStore(path=path)
    store.append(MemoryRecord(session_id="s", text="durable"))
    store.close()

    reopened = SqliteMemoryStore(path=path)
    rows = reopened.list_for_session("s")
    assert [r.text for r in rows] == ["durable"]
    reopened.close()


@pytest.mark.asyncio
async def test_provider_over_sqlite(tmp_path) -> None:
    """The store-backed provider works end-to-end over SQLite."""
    store = SqliteMemoryStore(path=str(tmp_path / "mem.sqlite3"))
    provider = StoreBackedMemoryProvider(store)
    await provider.sync_turn(
        MemoryTurn(session_id="s", assistant_text="Remember the gate code 4821.")
    )
    result = await provider.prefetch(RecallQuery(session_id="s", query="gate code"))
    assert len(result.records) == 1
    assert "4821" in result.records[0].text
    store.close()
