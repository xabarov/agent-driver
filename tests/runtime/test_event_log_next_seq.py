"""``next_seq`` peek semantics across every in-tree event-log backend.

C3 (compaction hardening): the store owns seq allocation as an O(1)/indexed
*peek*, replacing the old O(n)-per-emit ``max(seq)+1`` scan that materialized the
whole run log on every event. These tests pin the semantics the runtime relies on:

* peek is idempotent until an append (a caller may read it for a payload and again
  when stamping the event without opening a gap);
* peek advances by exactly one per appended event and matches the legacy scan;
* a cold backend instance seeds the high-water from the persisted log (resume);
* ``next_event_seq`` uses the fast path when present and falls back to a scan for a
  bare event-log duck type.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent, new_runtime_event
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore
from agent_driver.runtime.storage import RuntimeEventLog, next_event_seq
from agent_driver.runtime.storage.jsonl_store import JsonlEventLog


def _event(run_id: str, seq: int) -> RuntimeEvent:
    return new_runtime_event(
        event_type=RuntimeEventType.NODE_COMPLETED,
        context={"run_id": run_id, "attempt_id": "att", "seq": seq},
    )


def _legacy_next_seq(log: RuntimeEventLog, run_id: str) -> int:
    """The pre-C3 O(n) scan, kept here purely as the parity oracle."""
    events = log.list_for_run(run_id)
    return (max(event.seq for event in events) + 1) if events else 1


# Backend factories: name -> callable producing a fresh RuntimeEventLog.
# Postgres is live-only (needs a DSN) and excluded from the default sweep.
def _make_backends(tmp_path) -> dict[str, Callable[[], RuntimeEventLog]]:
    return {
        "in_memory": InMemoryEventLog,
        "sqlite": lambda: SqliteRuntimeStore(path=str(tmp_path / "seq.db")),
        "jsonl": lambda: JsonlEventLog(tmp_path / "jsonl"),
    }


@pytest.fixture(params=["in_memory", "sqlite", "jsonl"])
def backend(request, tmp_path) -> Iterator[RuntimeEventLog]:
    yield _make_backends(tmp_path)[request.param]()


def test_empty_log_peeks_one(backend: RuntimeEventLog) -> None:
    assert backend.next_seq("run") == 1


def test_peek_is_idempotent_until_append(backend: RuntimeEventLog) -> None:
    # Reading twice without an append must return the same value — this is what
    # lets _emit_runtime_decision read the seq for its payload and _emit read it
    # again when stamping the event without producing a gap.
    assert backend.next_seq("run") == 1
    assert backend.next_seq("run") == 1


def test_peek_advances_by_one_and_matches_legacy(backend: RuntimeEventLog) -> None:
    for expected in (1, 2, 3, 4):
        assert backend.next_seq("run") == expected
        assert next_event_seq(backend, "run") == expected
        assert _legacy_next_seq(backend, "run") == expected
        backend.append(_event("run", expected))
    # After four appends the log has seqs 1..4; next is 5 on every path.
    assert backend.next_seq("run") == 5
    assert _legacy_next_seq(backend, "run") == 5


def test_next_seq_is_per_run(backend: RuntimeEventLog) -> None:
    backend.append(_event("run_a", 1))
    backend.append(_event("run_a", 2))
    assert backend.next_seq("run_a") == 3
    assert backend.next_seq("run_b") == 1  # untouched run unaffected


def test_out_of_order_append_tracks_high_water(backend: RuntimeEventLog) -> None:
    backend.append(_event("run", 5))
    backend.append(_event("run", 2))  # lower seq must not lower the high-water
    assert backend.next_seq("run") == 6


def test_cold_instance_seeds_from_persisted_log(tmp_path) -> None:
    """A fresh backend over the same durable store recovers the high-water."""
    for name, make in _make_backends(tmp_path).items():
        if name == "in_memory":
            continue  # no durable substrate to reopen
        first = make()
        first.append(_event("run", 1))
        first.append(_event("run", 2))
        second = make()  # cold reopen of the same file/db
        assert second.next_seq("run") == 3, name
        assert _legacy_next_seq(second, "run") == 3, name


class _BareEventLog:
    """An event log duck type WITHOUT ``next_seq`` (pre-C3 external backend)."""

    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []

    def append(self, event: RuntimeEvent) -> None:
        self._events.append(event)

    def list_for_run(self, run_id, *, after_seq=None):
        return [e for e in self._events if e.run_id == run_id]


def test_next_event_seq_falls_back_without_next_seq() -> None:
    log = _BareEventLog()  # intentionally lacks next_seq — exercises the fallback
    assert next_event_seq(log, "run") == 1  # type: ignore[arg-type]
    log.append(_event("run", 1))
    log.append(_event("run", 2))
    assert next_event_seq(log, "run") == 3  # type: ignore[arg-type]
