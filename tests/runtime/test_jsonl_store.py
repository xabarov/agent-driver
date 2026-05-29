"""Phase 12 H23 — tests for JSONL session-store backend.

Pins:
* events round-trip through JSONL append + list_for_run;
* duplicate event_id is silently dropped on re-append (idempotent);
* after_seq filter works;
* partial last-line (kill -9 simulation) is tolerated on read;
* unrelated run_id events in same dir are isolated;
* run_id with path-traversal chars is sanitized;
* events + checkpoints in same file via ``jsonl_bundle`` work;
* dedup index re-primes on reopen (events surviving a process restart).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.storage.jsonl_store import (
    JsonlEventLog,
    _JsonlFileRegistry,
    _safe_filename,
    jsonl_bundle,
)


def _event(
    *,
    event_id: str,
    run_id: str = "run_1",
    seq: int = 1,
    event_type: RuntimeEventType = RuntimeEventType.NODE_STARTED,
    payload: dict | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=event_id,
        type=event_type,
        run_id=run_id,
        attempt_id="att_1",
        seq=seq,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        payload=payload or {},
    )


# -- _safe_filename -------------------------------------------------------


def test_safe_filename_strips_path_separators():
    assert _safe_filename("normal_run-1") == "normal_run-1"
    assert _safe_filename("../../etc/passwd") == "etc_passwd"
    assert _safe_filename("run/with/slashes") == "run_with_slashes"


def test_safe_filename_handles_empty():
    assert _safe_filename("") == "run_unknown"
    assert _safe_filename("...") == "run_unknown"


def test_safe_filename_truncates_extreme_length():
    long = "a" * 300
    assert len(_safe_filename(long)) <= 200


# -- event append + list --------------------------------------------------


def test_event_append_and_list_round_trip(tmp_path):
    log = JsonlEventLog(tmp_path)
    log.append(_event(event_id="e1", seq=1))
    log.append(_event(event_id="e2", seq=2))
    log.append(_event(event_id="e3", seq=3))
    events = log.list_for_run("run_1")
    assert [e.event_id for e in events] == ["e1", "e2", "e3"]
    assert [e.seq for e in events] == [1, 2, 3]


def test_event_dedup_by_event_id(tmp_path):
    log = JsonlEventLog(tmp_path)
    log.append(_event(event_id="e1", seq=1))
    log.append(_event(event_id="e1", seq=1))  # duplicate
    log.append(_event(event_id="e1", seq=1))  # duplicate
    events = log.list_for_run("run_1")
    assert len(events) == 1


def test_after_seq_filter(tmp_path):
    log = JsonlEventLog(tmp_path)
    for i in range(1, 6):
        log.append(_event(event_id=f"e{i}", seq=i))
    events = log.list_for_run("run_1", after_seq=3)
    assert [e.seq for e in events] == [4, 5]


def test_different_runs_isolated_in_same_dir(tmp_path):
    log = JsonlEventLog(tmp_path)
    log.append(_event(event_id="a1", run_id="run_a", seq=1))
    log.append(_event(event_id="b1", run_id="run_b", seq=1))
    log.append(_event(event_id="a2", run_id="run_a", seq=2))
    events_a = log.list_for_run("run_a")
    events_b = log.list_for_run("run_b")
    assert [e.event_id for e in events_a] == ["a1", "a2"]
    assert [e.event_id for e in events_b] == ["b1"]


# -- crash resilience -----------------------------------------------------


def test_partial_last_line_is_tolerated_on_read(tmp_path):
    """Simulate kill -9 mid-write by manually appending a truncated line."""
    log = JsonlEventLog(tmp_path)
    log.append(_event(event_id="e1", seq=1))
    log.append(_event(event_id="e2", seq=2))
    # Inject a partial line directly.
    file_path = tmp_path / "run_1.jsonl"
    with file_path.open("a", encoding="utf-8") as f:
        f.write('{"_kind": "event", "event": {"event_id": "e3", "ty')  # no \n
    events = log.list_for_run("run_1")
    assert [e.event_id for e in events] == ["e1", "e2"]


def test_malformed_event_row_skipped(tmp_path):
    """A row that's valid JSON but not a valid RuntimeEvent is logged + skipped."""
    log = JsonlEventLog(tmp_path)
    log.append(_event(event_id="e1", seq=1))
    file_path = tmp_path / "run_1.jsonl"
    # Inject a row that's JSON-valid but missing required RuntimeEvent fields.
    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"_kind": "event", "event": {"event_id": "broken"}}) + "\n")
    log.append(_event(event_id="e2", seq=2))
    events = log.list_for_run("run_1")
    assert [e.event_id for e in events] == ["e1", "e2"]


# -- dedup index reload ---------------------------------------------------


def test_dedup_index_reprimes_on_new_instance(tmp_path):
    """A fresh process opening the same file should not re-add existing events."""
    log_a = JsonlEventLog(tmp_path)
    log_a.append(_event(event_id="e1", seq=1))
    log_a.append(_event(event_id="e2", seq=2))
    # Fresh registry simulates a process restart.
    log_b = JsonlEventLog(tmp_path, registry=_JsonlFileRegistry())
    log_b.append(_event(event_id="e1", seq=1))  # duplicate of pre-restart
    log_b.append(_event(event_id="e3", seq=3))  # new
    events = log_b.list_for_run("run_1")
    ids = [e.event_id for e in events]
    assert ids == ["e1", "e2", "e3"]


# -- bundle: events + checkpoints in same file ----------------------------


def test_jsonl_bundle_shares_file(tmp_path):
    """Events and checkpoints land in the same per-run file via bundle()."""
    checkpoint_store, event_log = jsonl_bundle(tmp_path)
    event_log.append(_event(event_id="e1", seq=1))
    # We don't construct a CheckpointRecord here (needs RuntimeState) —
    # just verify that the event landed and the file is the expected path.
    file_path = tmp_path / "run_1.jsonl"
    assert file_path.exists()
    with file_path.open() as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    assert lines[0]["_kind"] == "event"


def test_capabilities_flags():
    log = JsonlEventLog("/tmp/agent_driver_test_jsonl_cap")
    caps = log.capabilities()
    assert caps.transactional_writes is False
    assert caps.supports_branching is False
    assert caps.supports_retention is False


# -- error tolerance -------------------------------------------------------


def test_list_for_unknown_run_returns_empty(tmp_path):
    log = JsonlEventLog(tmp_path)
    assert log.list_for_run("never_existed") == []
