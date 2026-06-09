"""Tests for the scheduler tick, hard interrupt and failure handling."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_driver.scheduler import (
    InMemoryJobStore,
    ScheduledJob,
    Scheduler,
    SqliteJobStore,
)


def _dt(d: int, h: int, mi: int = 0) -> datetime:
    return datetime(2026, 6, d, h, mi, tzinfo=timezone.utc)


def _job(name: str, schedule: str, **kw) -> ScheduledJob:
    return ScheduledJob(job_name=name, schedule=schedule, command="run", **kw)


@pytest.mark.asyncio
async def test_ensure_scheduled_seeds_next_run() -> None:
    store = InMemoryJobStore()
    store.add(_job("hourly", "@hourly"))
    sched = Scheduler(store, lambda job: asyncio.sleep(0))
    sched.ensure_scheduled(_dt(9, 12, 30))
    assert store.get("hourly").next_run_at == _dt(9, 13, 0)


@pytest.mark.asyncio
async def test_due_job_fires_and_reschedules() -> None:
    fired: list[str] = []

    async def runner(job: ScheduledJob) -> None:
        fired.append(job.command)

    store = InMemoryJobStore()
    store.add(_job("m", "* * * * *", next_run_at=_dt(9, 12, 0)))
    sched = Scheduler(store, runner)

    reports = await sched.tick(_dt(9, 12, 0))
    assert [r.status for r in reports] == ["ok"]
    assert fired == ["run"]
    job = store.get("m")
    assert job.run_count == 1
    assert job.last_status == "ok"
    # Next run advanced strictly past now (no backfill).
    assert job.next_run_at == _dt(9, 12, 1)


@pytest.mark.asyncio
async def test_not_due_job_does_not_fire() -> None:
    fired: list[str] = []
    store = InMemoryJobStore()
    store.add(_job("later", "@hourly", next_run_at=_dt(9, 14, 0)))
    sched = Scheduler(store, lambda job: fired.append("x"))  # type: ignore[arg-type]
    reports = await sched.tick(_dt(9, 12, 0))
    assert reports == []
    assert fired == []


@pytest.mark.asyncio
async def test_hard_interrupt_on_runaway_job() -> None:
    async def runaway(job: ScheduledJob) -> None:
        await asyncio.sleep(60)  # would block forever relative to the timeout

    store = InMemoryJobStore()
    store.add(_job("slow", "* * * * *", next_run_at=_dt(9, 12, 0)))
    sched = Scheduler(store, runaway, default_timeout_seconds=0.05)

    reports = await sched.tick(_dt(9, 12, 0))
    assert reports[0].status == "timeout"
    job = store.get("slow")
    assert job.last_status == "timeout"
    assert job.consecutive_failures == 1
    # Still rescheduled despite the timeout.
    assert job.next_run_at == _dt(9, 12, 1)


@pytest.mark.asyncio
async def test_error_records_and_reschedules() -> None:
    async def boom(job: ScheduledJob) -> None:
        raise RuntimeError("kaboom")

    store = InMemoryJobStore()
    store.add(_job("err", "* * * * *", next_run_at=_dt(9, 12, 0)))
    sched = Scheduler(store, boom)
    reports = await sched.tick(_dt(9, 12, 0))
    assert reports[0].status == "error"
    assert "kaboom" in reports[0].detail
    assert store.get("err").next_run_at == _dt(9, 12, 1)


@pytest.mark.asyncio
async def test_auto_disable_after_consecutive_failures() -> None:
    async def boom(job: ScheduledJob) -> None:
        raise RuntimeError("nope")

    store = InMemoryJobStore()
    store.add(_job("flaky", "* * * * *", next_run_at=_dt(9, 12, 0)))
    sched = Scheduler(store, boom, max_consecutive_failures=2)

    await sched.tick(_dt(9, 12, 0))
    assert store.get("flaky").enabled is True  # 1 failure
    await sched.tick(_dt(9, 12, 1))
    job = store.get("flaky")
    assert job.enabled is False  # 2 failures -> disabled
    assert job.consecutive_failures == 2
    # Disabled job is no longer due.
    assert store.due(_dt(9, 13, 0)) == []


@pytest.mark.asyncio
async def test_success_resets_failure_streak() -> None:
    calls = {"n": 0}

    async def flaky(job: ScheduledJob) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first fails")

    store = InMemoryJobStore()
    store.add(_job("f", "* * * * *", next_run_at=_dt(9, 12, 0)))
    sched = Scheduler(store, flaky)
    await sched.tick(_dt(9, 12, 0))
    assert store.get("f").consecutive_failures == 1
    await sched.tick(_dt(9, 12, 1))
    assert store.get("f").consecutive_failures == 0
    assert store.get("f").last_status == "ok"


@pytest.mark.asyncio
async def test_invalid_schedule_disables_job() -> None:
    store = InMemoryJobStore()
    store.add(_job("bad", "not a schedule"))
    sched = Scheduler(store, lambda job: asyncio.sleep(0))
    sched.ensure_scheduled(_dt(9, 12, 0))
    job = store.get("bad")
    assert job.enabled is False
    assert "invalid schedule" in job.last_error


@pytest.mark.asyncio
async def test_durable_scheduler_survives_restart(tmp_path) -> None:
    """A job registered before a 'restart' still fires from the reopened store."""
    path = str(tmp_path / "jobs.sqlite3")
    store = SqliteJobStore(path=path)
    store.add(_job("nightly", "* * * * *", next_run_at=_dt(9, 12, 0)))
    store.close()

    fired: list[str] = []

    async def runner(job: ScheduledJob) -> None:
        fired.append(job.job_name)

    reopened = SqliteJobStore(path=path)
    sched = Scheduler(reopened, runner)
    reports = await sched.tick(_dt(9, 12, 0))
    assert [r.status for r in reports] == ["ok"]
    assert fired == ["nightly"]
    assert reopened.get("nightly").run_count == 1
    reopened.close()
