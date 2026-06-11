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
from collections.abc import Awaitable, Callable, Iterable, Sequence
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
        retries: int = 0,
        retry_backoff_s: float = 0.5,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], "Awaitable[None]"] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if retries < 0:
            raise ValueError("retries must be >= 0")
        self._agent = agent
        self._concurrency = concurrency
        self._retries = retries
        self._retry_backoff_s = retry_backoff_s
        self._now = now or time.monotonic
        self._sleep = sleep or asyncio.sleep

    async def run(
        self,
        items: Iterable[BatchItem],
        *,
        store: TrajectoryStore | None = None,
        resume: bool = False,
        repeats: int = 1,
        max_total_cost_usd: float | None = None,
    ) -> BatchReport:
        """Run all items concurrently and return an aggregate report.

        ``repeats`` runs each item N times (``run_index`` 0..N-1) for N-run
        reliability; the returned report and any store then hold ``items ×
        repeats`` trajectories. Resume-skipping keys on ``item_id`` (a resumed
        item is skipped for all repeats).

        ``max_total_cost_usd`` caps cumulative estimated spend: once exceeded,
        not-yet-started runs are recorded as ``status="skipped_budget"`` instead
        of querying. The cap is best-effort under concurrency (in-flight runs
        finish), so actual spend may exceed it by up to one wave of runs.
        """
        if repeats < 1:
            raise ValueError("repeats must be >= 1")
        pending = list(items)
        skipped = 0
        if store is not None and resume:
            done = store.item_ids()
            before = len(pending)
            pending = [item for item in pending if item.item_id not in done]
            skipped = before - len(pending)

        semaphore = asyncio.Semaphore(self._concurrency)
        spent = 0.0

        async def _run_one(item: BatchItem, run_index: int) -> Trajectory:
            nonlocal spent
            async with semaphore:
                if max_total_cost_usd is not None and spent >= max_total_cost_usd:
                    trajectory = Trajectory(
                        item_id=item.item_id,
                        run_id=f"batch_{item.item_id}_{run_index}",
                        status="skipped_budget",
                        run_index=run_index,
                        metadata=item.metadata,
                    )
                else:
                    trajectory = await self._run_item(item, run_index=run_index)
                    spent += trajectory.cost_usd or 0.0
            if store is not None:
                store.append(trajectory)
            return trajectory

        trajectories = await asyncio.gather(
            *(
                _run_one(item, run_index)
                for item in pending
                for run_index in range(repeats)
            )
        )
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
        # Retry transient failures (network / provider 429 surface as raised
        # exceptions) with exponential backoff; a non-raising completed-with-
        # failure output is a real result and is not retried.
        last_exc: Exception | None = None
        output = None
        for attempt in range(self._retries + 1):
            try:
                output = await self._agent.query(item.input, run_id=run_id)
                last_exc = None
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_exc = exc
                # Fail fast on non-transient errors (auth, billing/402,
                # model-not-found, content policy, context overflow) — retrying
                # them just wastes attempts + backoff. Retry only transient ones
                # (rate-limit/429, overload, timeout, server, transport).
                if attempt >= self._retries or not _is_transient(exc):
                    break
                await self._sleep(self._retry_backoff_s * (2**attempt))
        if last_exc is not None:
            # One bad item must not abort the batch.
            return Trajectory.from_error(
                item.item_id,
                run_id,
                f"{type(last_exc).__name__}: {last_exc}",
                metadata=item.metadata,
                run_index=run_index,
                latency_ms=(self._now() - started) * 1000.0,
            )
        assert output is not None
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


def _is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` is a transient failure worth retrying.

    Uses the provider error classifier: retry rate-limit / overload / timeout /
    server / transport; fail fast on auth, billing (402), model-not-found,
    content-policy, payload-too-large, and context-overflow (which a plain retry
    won't fix).
    """
    from agent_driver.llm.error_classifier import (  # pylint: disable=import-outside-toplevel
        RecoveryAction,
        classify,
    )

    action = classify(exc).action
    return action not in (RecoveryAction.FAIL_FAST, RecoveryAction.COMPRESS_CONTEXT)


def items_from_prompts(
    prompts: Sequence[str], *, prefix: str = "item"
) -> list[BatchItem]:
    """Build a batch from a list of prompt strings with generated ids."""
    return [
        BatchItem(item_id=f"{prefix}_{index}", input=prompt)
        for index, prompt in enumerate(prompts)
    ]


__all__ = ["BatchRunner", "items_from_prompts"]
