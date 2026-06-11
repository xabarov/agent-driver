"""Pluggable keyed record store for server-side state (durable or in-memory).

The HTTP server keeps several keyed record maps — chat sessions
(``X-Session-Id``), stored responses (``previous_response_id`` chaining), and
A2A tasks. By default these live in a bounded in-memory LRU (lost on restart);
a :class:`SqliteRecordStore` makes them durable so a long-lived server survives
a restart.

Records are JSON-serializable values (dict / list of primitives); each caller
serializes its own record type to/from that shape, so both backends behave
identically. Keyed by ``(namespace, key)`` so one store backs all maps.

Lives in ``persistence`` (stdlib-only, no heavy imports) so the dependency-free
adapter cores can use it without pulling the server's ASGI stack.

Note: async *runs* are intentionally not persisted here — a run's in-flight
background task cannot survive a process restart, so durable long-running work
belongs to the runtime checkpoint/event-log layer, not this record store.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from agent_driver.persistence.sqlite import SqliteStoreBase

DEFAULT_MAX_PER_NAMESPACE = 1024


@runtime_checkable
class RecordStore(Protocol):
    """A keyed store of JSON-serializable records, namespaced per map."""

    def get(self, namespace: str, key: str) -> Any | None:
        """Return the record at ``(namespace, key)`` or ``None``."""

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Store ``value`` (JSON-serializable) at ``(namespace, key)``."""

    def delete(self, namespace: str, key: str) -> bool:
        """Remove ``(namespace, key)``; return whether it existed."""


class InMemoryRecordStore:
    """Process-local bounded-LRU record store (records lost on restart)."""

    def __init__(self, *, max_per_namespace: int = DEFAULT_MAX_PER_NAMESPACE) -> None:
        self._max = max(1, max_per_namespace)
        self._maps: dict[str, "OrderedDict[str, Any]"] = {}
        self._lock = RLock()

    def _ns(self, namespace: str) -> "OrderedDict[str, Any]":
        return self._maps.setdefault(namespace, OrderedDict())

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            ns = self._ns(namespace)
            if key not in ns:
                return None
            ns.move_to_end(key)
            return ns[key]

    def set(self, namespace: str, key: str, value: Any) -> None:
        with self._lock:
            ns = self._ns(namespace)
            ns[key] = value
            ns.move_to_end(key)
            while len(ns) > self._max:
                ns.popitem(last=False)

    def delete(self, namespace: str, key: str) -> bool:
        with self._lock:
            return self._ns(namespace).pop(key, None) is not None


class SqliteRecordStore(SqliteStoreBase):
    """Durable SQLite record store; survives restart, unbounded by design."""

    def _init_schema(self) -> None:
        self._execute(
            "CREATE TABLE IF NOT EXISTS server_records ("
            "namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
            "PRIMARY KEY (namespace, key))"
        )

    def get(self, namespace: str, key: str) -> Any | None:
        rows = self._query(
            "SELECT value FROM server_records WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        if not rows:
            return None
        return json.loads(rows[0][0])

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO server_records (namespace, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value",
            (namespace, key, json.dumps(value, ensure_ascii=False)),
        )

    def delete(self, namespace: str, key: str) -> bool:
        cursor = self._execute(
            "DELETE FROM server_records WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        return cursor.rowcount > 0


__all__ = [
    "DEFAULT_MAX_PER_NAMESPACE",
    "InMemoryRecordStore",
    "RecordStore",
    "SqliteRecordStore",
]
