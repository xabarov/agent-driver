"""Tests for the pluggable record store (in-memory LRU + durable SQLite)."""

from __future__ import annotations

from typing import Any

from agent_driver.persistence.record_store import (
    InMemoryRecordStore,
    SqliteRecordStore,
)


def test_in_memory_get_set_delete() -> None:
    store = InMemoryRecordStore()
    assert store.get("ns", "k") is None
    store.set("ns", "k", {"v": 1})
    assert store.get("ns", "k") == {"v": 1}
    assert store.delete("ns", "k") is True
    assert store.delete("ns", "k") is False
    assert store.get("ns", "k") is None


def test_in_memory_lru_eviction_per_namespace() -> None:
    store = InMemoryRecordStore(max_per_namespace=2)
    store.set("ns", "a", 1)
    store.set("ns", "b", 2)
    store.get("ns", "a")  # touch a -> b is now least-recent
    store.set("ns", "c", 3)  # over cap -> evict b
    assert store.get("ns", "a") == 1
    assert store.get("ns", "c") == 3
    assert store.get("ns", "b") is None
    # Namespaces are independent.
    store.set("other", "a", 9)
    assert store.get("other", "a") == 9


def test_sqlite_roundtrip_and_namespaces(tmp_path: Any) -> None:
    path = str(tmp_path / "rec.db")
    store = SqliteRecordStore(path=path)
    store.set("session", "s1", [{"role": "user", "content": "hi"}])
    store.set("response", "r1", {"id": "r1", "answer": "ok"})
    assert store.get("session", "s1") == [{"role": "user", "content": "hi"}]
    assert store.get("response", "r1") == {"id": "r1", "answer": "ok"}
    assert store.get("session", "missing") is None
    assert store.delete("response", "r1") is True
    assert store.get("response", "r1") is None
    store.close()


def test_sqlite_survives_reopen(tmp_path: Any) -> None:
    path = str(tmp_path / "rec.db")
    first = SqliteRecordStore(path=path)
    first.set("response", "r1", {"id": "r1", "messages": [{"role": "user"}]})
    first.close()

    # A fresh store on the same file (simulating a process restart) sees it.
    second = SqliteRecordStore(path=path)
    assert second.get("response", "r1") == {"id": "r1", "messages": [{"role": "user"}]}
    second.close()
