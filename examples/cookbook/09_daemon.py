"""Scheduler daemon: drive the production ``run_forever`` loop end-to-end.

Where ``04_scheduler.py`` drives ``tick`` by hand for determinism, this shows
the real long-running shape a host deploys: a ``JobRunner`` that executes an
actual agent turn, the scheduler's ``run_forever`` poll loop, and a graceful
``stop`` (here the job itself signals stop after the first fire; a real daemon
wires ``stop`` to SIGINT/SIGTERM). Bounded by a hard timeout so it always
terminates.

    python examples/cookbook/09_daemon.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agent_driver.scheduler import InMemoryJobStore, ScheduledJob, Scheduler
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.llm.providers_impl.fake import FakeProvider


async def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="report generated"), tools=ToolSet.only()
    )
    stop = asyncio.Event()
    answers: list[str] = []

    async def job_runner(job: ScheduledJob) -> None:
        # A real host runs the job's command as an agent turn.
        output = await agent.query(job.command, run_id=f"job-{job.run_count}")
        answers.append(output.answer or "")
        stop.set()  # one fire is enough for the demo; a daemon keeps polling

    store = InMemoryJobStore()
    # Seed next_run_at in the past so the job is due on the loop's first tick.
    due_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    store.add(
        ScheduledJob(
            job_name="nightly-report",
            schedule="every 1h",
            command="summarize today's activity",
            next_run_at=due_at,
        )
    )

    scheduler = Scheduler(store, job_runner)
    # run_forever polls until stop is set; the hard timeout guards the demo.
    await asyncio.wait_for(
        scheduler.run_forever(poll_interval_seconds=0.05, stop=stop), timeout=5.0
    )

    job = store.get("nightly-report")
    print("answers:", answers)
    print("run_count:", job.run_count, "last_status:", job.last_status)


if __name__ == "__main__":
    asyncio.run(main())
