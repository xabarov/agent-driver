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
import time
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

from agent_driver.batch.contracts import BatchItem, BatchReport, Trajectory
from agent_driver.batch.store import TrajectoryStore

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent


class BatchRunner:
    """Generate trajectories for a batch of prompts.

    Each trajectory carries per-task ``cost_usd`` (estimated from the run's
    usage) and ``latency_ms`` (wall-clock around the query), so a downstream
    aggregator can report median + percentile economics. ``now`` is injectable
    for deterministic latency in tests.
    """

    def __init__(
        self,
        agent: "Agent",
        *,
        concurrency: int = 4,
        now: Callable[[], float] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._agent = agent
        self._concurrency = concurrency
        self._now = now or time.monotonic

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

    async def _run_item(self, item: BatchItem, *, run_index: int = 0) -> Trajectory:
        run_id = (
            f"batch_{item.item_id}_{run_index}"
            if run_index
            else f"batch_{item.item_id}"
        )
        started = self._now()
        try:
            output = await self._agent.query(item.input, run_id=run_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # One bad item must not abort the batch.
            return Trajectory.from_error(
                item.item_id,
                run_id,
                f"{type(exc).__name__}: {exc}",
                metadata=item.metadata,
                run_index=run_index,
                latency_ms=(self._now() - started) * 1000.0,
            )
        latency_ms = (self._now() - started) * 1000.0
        # Local import: keep the observability subtree (which imports runtime)
        # off the batch-import path.
        from agent_driver.observability.cost_ledger import (  # pylint: disable=import-outside-toplevel
            estimate_cost_usd,
        )

        cost_usd = estimate_cost_usd(output.usage) if output.usage else None
        return Trajectory.from_output(
            item.item_id,
            output,
            metadata=item.metadata,
            run_index=run_index,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )


def items_from_prompts(
    prompts: Sequence[str], *, prefix: str = "item"
) -> list[BatchItem]:
    """Build a batch from a list of prompt strings with generated ids."""
    return [
        BatchItem(item_id=f"{prefix}_{index}", input=prompt)
        for index, prompt in enumerate(prompts)
    ]


__all__ = ["BatchRunner", "items_from_prompts"]
