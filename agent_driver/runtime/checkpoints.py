"""In-memory checkpoint backend for runtime skeleton."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.runtime.checkpoint_factory import (
    CheckpointChain,
    CheckpointSeed,
    build_checkpoint_ref,
)
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import (
    CheckpointRecord,
    CheckpointStore,
    StorageCapabilities,
)


def _seed_from_runtime_state(
    *,
    graph_id: str,
    node_id: str | None,
    storage_backend: str,
    run_id: str,
    state: RuntimeState,
) -> CheckpointSeed:
    """Build checkpoint seed from runtime state and target backend."""
    attempt_id = state.latest_output.attempt_id if state.latest_output else "attempt_1"
    return CheckpointSeed(
        run_id=run_id,
        attempt_id=attempt_id,
        thread_id=state.run_input.thread_id,
        graph_id=graph_id,
        node_id=node_id,
        storage_backend=storage_backend,
        prior_checkpoint_id=(
            state.checkpoint.checkpoint_id if state.checkpoint else None
        ),
    )


def _prepare_seed_and_previous(
    *,
    latest_loader: Callable[[str], CheckpointRecord | None],
    graph_id: str,
    node_id: str | None,
    storage_backend: str,
    state: RuntimeState,
) -> tuple[CheckpointSeed, CheckpointRecord | None]:
    """Build seed and resolve previous checkpoint row for save operation."""
    run_id = state.run_input.run_id or "run_unknown"
    previous = latest_loader(run_id)
    seed = _seed_from_runtime_state(
        graph_id=graph_id,
        node_id=node_id,
        storage_backend=storage_backend,
        run_id=run_id,
        state=state,
    )
    return seed, previous


class InMemoryCheckpointStore(CheckpointStore):
    """Simple in-memory checkpoint storage for fake runner tests."""

    def __init__(self) -> None:
        self._by_run: dict[str, list[CheckpointRecord]] = {}
        self._by_id: dict[str, CheckpointRecord] = {}

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        seed, previous = _prepare_seed_and_previous(
            latest_loader=self.latest,
            graph_id=graph_id,
            node_id=node_id,
            storage_backend="memory",
            state=state,
        )
        ref = build_checkpoint_ref(
            seed=seed,
            chain=CheckpointChain(previous_row=previous),
        )
        state = state.model_copy(update={"checkpoint": ref})
        row = CheckpointRecord(ref=ref, state=state)
        self._by_run.setdefault(seed.run_id, []).append(row)
        self._by_id[ref.checkpoint_id] = row
        return ref

    def latest(self, run_id: str) -> CheckpointRecord | None:
        """Return latest checkpoint row for run, if any."""
        rows = self._by_run.get(run_id, [])
        if not rows:
            return None
        return rows[-1]

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier, if any."""
        return self._by_id.get(checkpoint_id)

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        """Return checkpoints for run in newest-first order."""
        rows = list(reversed(self._by_run.get(run_id, [])))
        if limit is None:
            return rows
        return rows[:limit]

    def snapshot_debug(self) -> Mapping[str, list[CheckpointRecord]]:
        """Return debug snapshot of internal checkpoint rows."""
        return self._by_run

    def capabilities(self) -> StorageCapabilities:
        """Return capabilities for in-memory backend."""
        return StorageCapabilities(
            transactional_writes=False,
            supports_branching=False,
            supports_retention=False,
            supports_snapshot_debug=True,
        )
