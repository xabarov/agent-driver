"""Trajectory stores: in-memory and append-only JSONL (for resume)."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from agent_driver.batch.contracts import Trajectory


class TrajectoryStore(Protocol):
    """Sink for recorded trajectories that also reports what is already done."""

    def append(self, trajectory: Trajectory) -> None:
        """Persist one trajectory."""
        raise NotImplementedError

    def item_ids(self) -> set[str]:
        """Return item ids already recorded (for resume)."""
        raise NotImplementedError


class InMemoryTrajectoryStore:
    """Process-local trajectory sink."""

    def __init__(self) -> None:
        self._items: list[Trajectory] = []
        self._lock = RLock()

    def append(self, trajectory: Trajectory) -> None:
        """Persist one trajectory."""
        with self._lock:
            self._items.append(trajectory)

    def item_ids(self) -> set[str]:
        """Return recorded item ids."""
        with self._lock:
            return {traj.item_id for traj in self._items}

    def trajectories(self) -> list[Trajectory]:
        """Return all recorded trajectories."""
        with self._lock:
            return list(self._items)


class JsonlTrajectoryStore:
    """Append-only JSONL store — the canonical trajectory-dataset format.

    Each line is one trajectory (``model_dump(mode="json")``). Resume reads the
    existing ``item_id`` of every line so a re-run skips finished items.
    """

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trajectory: Trajectory) -> None:
        """Append one trajectory as a JSON line."""
        line = json.dumps(trajectory.model_dump(mode="json"), ensure_ascii=False)
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def item_ids(self) -> set[str]:
        """Return item ids already on disk (empty if the file is absent)."""
        if not self._path.exists():
            return set()
        ids: set[str] = set()
        with self._lock, self._path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line)["item_id"])
                except (ValueError, KeyError, TypeError):
                    continue
        return ids


__all__ = [
    "InMemoryTrajectoryStore",
    "JsonlTrajectoryStore",
    "TrajectoryStore",
]
