"""In-memory checkpoint backend for runtime skeleton."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.runtime.state import RuntimeState


@dataclass(frozen=True)
class StoredCheckpoint:
    """Checkpoint row pairing reference and runtime state payload."""

    ref: CheckpointRef
    state: RuntimeState


class InMemoryCheckpointStore:
    """Simple in-memory checkpoint storage for fake runner tests."""

    def __init__(self) -> None:
        self._by_run: dict[str, list[StoredCheckpoint]] = {}

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        run_id = state.run_input.run_id or "run_unknown"
        previous = self.latest(run_id)
        attempt_id = (
            state.latest_output.attempt_id if state.latest_output else "attempt_1"
        )
        ref = CheckpointRef(
            checkpoint_id=f"chk_{uuid4().hex}",
            run_id=run_id,
            attempt_id=attempt_id,
            thread_id=state.run_input.thread_id,
            branch_id=None,
            parent_checkpoint_id=(
                state.checkpoint.checkpoint_id
                if state.checkpoint
                else (previous.ref.checkpoint_id if previous else None)
            ),
            graph_id=graph_id,
            node_id=node_id,
            created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            state_version="v1",
            storage_backend="memory",
            metadata={},
        )
        row = StoredCheckpoint(ref=ref, state=state)
        self._by_run.setdefault(run_id, []).append(row)
        return ref

    def latest(self, run_id: str) -> StoredCheckpoint | None:
        """Return latest checkpoint row for run, if any."""
        rows = self._by_run.get(run_id, [])
        if not rows:
            return None
        return rows[-1]

    def snapshot(self) -> Mapping[str, list[StoredCheckpoint]]:
        """Return readonly snapshot of internal checkpoint rows."""
        return self._by_run
