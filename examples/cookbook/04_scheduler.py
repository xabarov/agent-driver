"""Scheduler: register a cron job and fire it deterministically.

The Scheduler is decoupled from what a job does — the host supplies a
``JobRunner``. ``tick(now)`` fires everything due at ``now`` with a per-run
hard timeout. Production hosts call ``run_forever``; here we drive ``tick``
with explicit times so it is deterministic.

    python examples/cookbook/04_scheduler.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agent_driver.scheduler import (
    InMemoryJobStore,
    ScheduledJob,
    Scheduler,
)


async def main() -> None:
    fired: list[str] = []

    async def job_runner(job: ScheduledJob) -> None:
        # A real host might do: await agent.query(job.command)
        fired.append(job.command)

    store = InMemoryJobStore()
    store.add(
        ScheduledJob(job_name="nightly-sync", schedule="@daily", command="sync data")
    )

    scheduler = Scheduler(store, job_runner)
    midnight = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    reports = await scheduler.tick(midnight)

    print("fired:", fired)
    print("reports:", [(r.job_name, r.status) for r in reports])
    job = store.get("nightly-sync")
    print("run_count:", job.run_count, "next_run_at:", job.next_run_at)


if __name__ == "__main__":
    asyncio.run(main())
