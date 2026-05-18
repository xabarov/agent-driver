"""Storage protocols for runtime checkpoints and events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.state import RuntimeState


@dataclass(frozen=True)
class CheckpointRecord:
    """Checkpoint row pairing reference and serialized runtime state."""

    ref: CheckpointRef
    state: RuntimeState


@dataclass(frozen=True)
class StorageCapabilities:
    """Backend capability flags used by runtime/docs selection logic."""

    transactional_writes: bool
    supports_branching: bool
    supports_retention: bool
    supports_snapshot_debug: bool = False


class CheckpointStore(Protocol):
    """Protocol for persisting and loading runtime checkpoints."""

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        raise NotImplementedError

    def latest(self, run_id: str) -> CheckpointRecord | None:
        """Return latest checkpoint row for run, if any."""
        raise NotImplementedError

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier, if any."""
        raise NotImplementedError

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        """Return checkpoints for one run ordered newest-first."""
        raise NotImplementedError

    def snapshot_debug(self) -> Mapping[str, list[CheckpointRecord]]:
        """Return debug-only snapshot of all checkpoint rows."""
        raise NotImplementedError

    def capabilities(self) -> StorageCapabilities:
        """Return backend capability flags for operators/tests."""
        raise NotImplementedError


class RuntimeEventLog(Protocol):
    """Protocol for append-only runtime event stores."""

    def append(self, event: RuntimeEvent) -> None:
        """Persist one runtime event."""
        raise NotImplementedError

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events, optionally filtering by sequence number."""
        raise NotImplementedError

    def capabilities(self) -> StorageCapabilities:
        """Return backend capability flags for operators/tests."""
        raise NotImplementedError
