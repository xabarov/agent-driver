"""Shared checkpoint reference factory for storage backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.runtime.storage import CheckpointRecord


@dataclass(frozen=True)
class CheckpointSeed:
    """Input fields required to construct a checkpoint reference."""

    run_id: str
    attempt_id: str
    thread_id: str | None
    graph_id: str
    node_id: str | None
    storage_backend: str
    prior_checkpoint_id: str | None


@dataclass(frozen=True)
class CheckpointChain:
    """Previous checkpoint row in same run, when available."""

    previous_row: CheckpointRecord | None


def build_checkpoint_ref(
    *,
    seed: CheckpointSeed,
    chain: CheckpointChain,
) -> CheckpointRef:
    """Build checkpoint reference with stable parent-chain logic."""
    return CheckpointRef(
        checkpoint_id=f"chk_{uuid4().hex}",
        run_id=seed.run_id,
        attempt_id=seed.attempt_id,
        thread_id=seed.thread_id,
        branch_id=None,
        parent_checkpoint_id=(
            seed.prior_checkpoint_id
            if seed.prior_checkpoint_id
            else (chain.previous_row.ref.checkpoint_id if chain.previous_row else None)
        ),
        graph_id=seed.graph_id,
        node_id=seed.node_id,
        created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        state_version="v1",
        storage_backend=seed.storage_backend,
        metadata={},
    )
