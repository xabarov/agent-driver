"""Phase 12 H23 — JSONL backend for RuntimeEventLog + CheckpointStore.

A cheap, single-file-per-session durable tier. One JSONL file per
``run_id`` (or shared session file for multi-run threads). Each line
is one JSON object — event or checkpoint row — tagged with a ``_kind``
discriminator so a single file can hold both.

Design highlights:

* **Append-only**: writes are line-appends with ``\n`` terminator. No
  in-place mutation. Crash-safe — a kill -9 mid-write at worst leaves
  a partial last line, which the reader skips silently.
* **Dedup by event_id**: when an event with the same ``event_id`` is
  appended twice (e.g. retry path), the second write is silently
  dropped. Implemented via an in-memory ``set`` keyed per file.
* **Tail-scan optimization**: ``list_for_run(after_seq=N)`` reads only
  the portion needed. For very large files this could use reverse
  seek; the current implementation reads sequentially because the
  expected per-run size is bounded.
* **Atomic file rotation safety**: no rotation in this first cut —
  files grow append-only.

Backwards compatibility: implements the same ``RuntimeEventLog`` and
``CheckpointStore`` protocols as the in-memory and postgres
backends. Same conformance tests can run against it.

Future H23b: per-file ``asyncio.Queue`` + 100 ms batched flush;
parent_event_id chain reconstruction with out-of-order tolerance;
reverse-seek tail-scan; lite-metadata tail read.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.checkpoint_factory import (
    CheckpointChain,
    CheckpointSeed,
    build_checkpoint_ref,
)
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage.protocols import (
    CheckpointRecord,
    CheckpointStore,
    RuntimeEventLog,
    StorageCapabilities,
)

logger = logging.getLogger(__name__)

KIND_EVENT = "event"
KIND_CHECKPOINT = "checkpoint"


def _safe_filename(run_id: str) -> str:
    """Map ``run_id`` to a safe filename component.

    Strips path separators and other characters that could escape the
    storage directory; rejects empty input.
    """
    cleaned = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in (run_id or "")
    ).strip("._-")
    if not cleaned:
        cleaned = "run_unknown"
    # Avoid extreme lengths on shared filesystems.
    return cleaned[:200]


def _serialize_event(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "_kind": KIND_EVENT,
        "event": event.model_dump(mode="json"),
    }


def _serialize_checkpoint(record: CheckpointRecord) -> dict[str, Any]:
    return {
        "_kind": KIND_CHECKPOINT,
        "ref": record.ref.model_dump(mode="json"),
        "state": record.state.model_dump(mode="json"),
    }


def _deserialize_event(payload: dict[str, Any]) -> RuntimeEvent | None:
    raw = payload.get("event")
    if not isinstance(raw, dict):
        return None
    try:
        return RuntimeEvent.model_validate(raw)
    except Exception:
        logger.warning("jsonl_store: skipping malformed event row", exc_info=True)
        return None


def _deserialize_checkpoint(payload: dict[str, Any]) -> CheckpointRecord | None:
    raw_ref = payload.get("ref")
    raw_state = payload.get("state")
    if not isinstance(raw_ref, dict) or not isinstance(raw_state, dict):
        return None
    try:
        ref = CheckpointRef.model_validate(raw_ref)
        state = RuntimeState.model_validate(raw_state)
    except Exception:
        logger.warning(
            "jsonl_store: skipping malformed checkpoint row", exc_info=True
        )
        return None
    return CheckpointRecord(ref=ref, state=state)


class _JsonlFile:
    """Append helper for one session file with in-memory event_id dedup."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seen_event_ids: set[str] = set()
        if self._path.exists():
            self._prime_seen_event_ids()

    @property
    def path(self) -> Path:
        return self._path

    def _prime_seen_event_ids(self) -> None:
        """Pre-load event IDs already present in the file."""
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        # Last partial line from crash; ignore.
                        continue
                    if payload.get("_kind") != KIND_EVENT:
                        continue
                    raw = payload.get("event") or {}
                    event_id = raw.get("event_id") if isinstance(raw, dict) else None
                    if isinstance(event_id, str) and event_id:
                        self._seen_event_ids.add(event_id)
        except OSError as exc:
            logger.warning(
                "jsonl_store: failed to prime event_id index from %s: %s",
                self._path,
                exc,
            )

    def append_event(self, event: RuntimeEvent) -> bool:
        """Append one event row. Returns False if dedup dropped it."""
        with self._lock:
            if event.event_id and event.event_id in self._seen_event_ids:
                return False
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = _serialize_event(event)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True))
                f.write("\n")
            if event.event_id:
                self._seen_event_ids.add(event.event_id)
            return True

    def append_checkpoint(self, record: CheckpointRecord) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = _serialize_checkpoint(record)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True))
                f.write("\n")

    def iter_rows(self) -> list[dict[str, Any]]:
        """Read all rows in append order. Skips malformed lines silently."""
        rows: list[dict[str, Any]] = []
        if not self._path.exists():
            return rows
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        # Trailing partial line from a kill -9 — skip silently.
                        continue
                    rows.append(payload)
        except OSError as exc:
            logger.warning("jsonl_store: read failed for %s: %s", self._path, exc)
        return rows


class _JsonlFileRegistry:
    """Shared registry of ``_JsonlFile`` instances keyed by absolute path.

    Ensures the in-memory dedup index is shared between the event log
    and checkpoint store when both target the same session file.
    """

    def __init__(self) -> None:
        self._files: dict[str, _JsonlFile] = {}
        self._lock = threading.Lock()

    def get(self, path: Path) -> _JsonlFile:
        key = str(path.resolve())
        with self._lock:
            existing = self._files.get(key)
            if existing is not None:
                return existing
            handle = _JsonlFile(path)
            self._files[key] = handle
            return handle


class JsonlEventLog(RuntimeEventLog):
    """JSONL-backed event log.

    One file per ``run_id`` at ``storage_dir/{safe(run_id)}.jsonl``.
    Events are appended in order; duplicates (same ``event_id``)
    silently dropped.
    """

    def __init__(
        self,
        storage_dir: str | os.PathLike,
        *,
        registry: _JsonlFileRegistry | None = None,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._registry = registry or _JsonlFileRegistry()

    def _file_for_run(self, run_id: str) -> _JsonlFile:
        filename = _safe_filename(run_id) + ".jsonl"
        return self._registry.get(self._storage_dir / filename)

    def append(self, event: RuntimeEvent) -> None:
        self._file_for_run(event.run_id).append_event(event)

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        handle = self._file_for_run(run_id)
        events: list[RuntimeEvent] = []
        for row in handle.iter_rows():
            if row.get("_kind") != KIND_EVENT:
                continue
            event = _deserialize_event(row)
            if event is None or event.run_id != run_id:
                continue
            if after_seq is not None and event.seq <= after_seq:
                continue
            events.append(event)
        return events

    def capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            transactional_writes=False,
            supports_branching=False,
            supports_retention=False,
            supports_snapshot_debug=False,
        )


class JsonlCheckpointStore(CheckpointStore):
    """JSONL-backed checkpoint store.

    Shares the underlying file with ``JsonlEventLog`` when constructed
    with the same registry and storage_dir, so a single ``.jsonl``
    holds both events and checkpoint snapshots — matching openclaude's
    "one file per session" convention.
    """

    def __init__(
        self,
        storage_dir: str | os.PathLike,
        *,
        registry: _JsonlFileRegistry | None = None,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._registry = registry or _JsonlFileRegistry()

    def _file_for_run(self, run_id: str) -> _JsonlFile:
        filename = _safe_filename(run_id) + ".jsonl"
        return self._registry.get(self._storage_dir / filename)

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        run_id = state.run_input.run_id or "run_unknown"
        previous = self.latest(run_id)
        attempt_id = (
            state.latest_output.attempt_id if state.latest_output else "attempt_1"
        )
        seed = CheckpointSeed(
            run_id=run_id,
            attempt_id=attempt_id,
            thread_id=state.run_input.thread_id,
            graph_id=graph_id,
            node_id=node_id,
            storage_backend="jsonl",
            prior_checkpoint_id=(
                state.checkpoint.checkpoint_id if state.checkpoint else None
            ),
        )
        ref = build_checkpoint_ref(
            seed=seed,
            chain=CheckpointChain(previous_row=previous),
        )
        new_state = state.model_copy(update={"checkpoint": ref})
        record = CheckpointRecord(ref=ref, state=new_state)
        self._file_for_run(run_id).append_checkpoint(record)
        return ref

    def latest(self, run_id: str) -> CheckpointRecord | None:
        records = self.list_checkpoints(run_id, limit=1)
        return records[0] if records else None

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        # Scan all known files until we find a match. For first-cut
        # the storage directory is typically small (one file per
        # active run); a fuller index lives in H23b.
        if not self._storage_dir.exists():
            return None
        try:
            paths = sorted(self._storage_dir.glob("*.jsonl"))
        except OSError:
            return None
        for path in paths:
            handle = self._registry.get(path)
            for row in handle.iter_rows():
                if row.get("_kind") != KIND_CHECKPOINT:
                    continue
                record = _deserialize_checkpoint(row)
                if record is None:
                    continue
                if record.ref.checkpoint_id == checkpoint_id:
                    return record
        return None

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        handle = self._file_for_run(run_id)
        records: list[CheckpointRecord] = []
        for row in handle.iter_rows():
            if row.get("_kind") != KIND_CHECKPOINT:
                continue
            record = _deserialize_checkpoint(row)
            if record is None:
                continue
            if record.ref.run_id != run_id:
                continue
            records.append(record)
        records.reverse()  # newest-first
        if limit is None:
            return records
        return records[:limit]

    def snapshot_debug(self) -> Mapping[str, list[CheckpointRecord]]:
        out: dict[str, list[CheckpointRecord]] = {}
        if not self._storage_dir.exists():
            return out
        try:
            paths = sorted(self._storage_dir.glob("*.jsonl"))
        except OSError:
            return out
        for path in paths:
            handle = self._registry.get(path)
            for row in handle.iter_rows():
                if row.get("_kind") != KIND_CHECKPOINT:
                    continue
                record = _deserialize_checkpoint(row)
                if record is None:
                    continue
                out.setdefault(record.ref.run_id, []).append(record)
        return out

    def capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            transactional_writes=False,
            supports_branching=False,
            supports_retention=False,
            supports_snapshot_debug=True,
        )


def jsonl_bundle(
    storage_dir: str | os.PathLike,
) -> tuple[JsonlCheckpointStore, JsonlEventLog]:
    """Convenience factory that shares one ``_JsonlFileRegistry`` so
    both stores write to the same per-run file."""
    registry = _JsonlFileRegistry()
    return (
        JsonlCheckpointStore(storage_dir, registry=registry),
        JsonlEventLog(storage_dir, registry=registry),
    )


__all__ = [
    "JsonlCheckpointStore",
    "JsonlEventLog",
    "jsonl_bundle",
]
