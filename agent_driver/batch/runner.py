"""Concurrent batch trajectory generation over an :class:`Agent`.

Runs a dataset of prompts through the agent with bounded concurrency, records
one :class:`Trajectory` per item (failures isolated), optionally persists to a
:class:`TrajectoryStore` (which also enables resume — already-recorded items
are skipped), and aggregates a :class:`BatchReport`.

Builds straight on the SDK (``agent.query``) and the descriptor-resolved
provider, so it stays runtime-neutral.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from agent_driver.batch.contracts import BatchItem, BatchReport, Trajectory
from agent_driver.batch.store import TrajectoryStore

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent


class BatchRunner:
    """Generate trajectories for a batch of prompts."""

    def __init__(self, agent: "Agent", *, concurrency: int = 4) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._agent = agent
        self._concurrency = concurrency

    async def run(
        self,
        items: Iterable[BatchItem],
        *,
        store: TrajectoryStore | None = None,
        resume: bool = False,
    ) -> BatchReport:
        """Run all items concurrently and return an aggregate report."""
        pending = list(items)
        skipped = 0
        if store is not None and resume:
            done = store.item_ids()
            before = len(pending)
            pending = [item for item in pending if item.item_id not in done]
            skipped = before - len(pending)

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _run_one(item: BatchItem) -> Trajectory:
            async with semaphore:
                trajectory = await self._run_item(item)
            if store is not None:
                store.append(trajectory)
            return trajectory

        trajectories = await asyncio.gather(*(_run_one(item) for item in pending))
        return BatchReport.from_trajectories(
            list(trajectories), skipped_resumed=skipped
        )

    async def _run_item(self, item: BatchItem) -> Trajectory:
        run_id = f"batch_{item.item_id}"
        try:
            output = await self._agent.query(item.input, run_id=run_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # One bad item must not abort the batch.
            return Trajectory.from_error(
                item.item_id,
                run_id,
                f"{type(exc).__name__}: {exc}",
                metadata=item.metadata,
            )
        return Trajectory.from_output(item.item_id, output, metadata=item.metadata)


def items_from_prompts(
    prompts: Sequence[str], *, prefix: str = "item"
) -> list[BatchItem]:
    """Build a batch from a list of prompt strings with generated ids."""
    return [
        BatchItem(item_id=f"{prefix}_{index}", input=prompt)
        for index, prompt in enumerate(prompts)
    ]


__all__ = ["BatchRunner", "items_from_prompts"]
