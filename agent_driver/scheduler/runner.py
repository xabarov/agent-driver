"""Durable scheduler: fire due jobs with a per-run hard interrupt.

The scheduler is deliberately decoupled from what a job *does*: the host
supplies a :data:`JobRunner` callback (e.g. one that runs ``agent.query`` with
the job's ``command``). :meth:`Scheduler.tick` is deterministic — it takes the
reference time as an argument — so behavior is unit-testable without real
clocks; :meth:`Scheduler.run_forever` is the thin always-on driver.

Design choices:
- **Hard interrupt**: each run is bounded by ``asyncio.wait_for`` so a runaway
  job cannot block the loop.
- **No backfill**: a job that missed many windows fires once, then its next
  run is computed from *now* — not once per missed window.
- **Failure auto-disable**: after ``max_consecutive_failures`` the job is
  disabled so it cannot spin.
- **Robust scheduling**: an unparseable schedule disables the job instead of
  crashing the tick.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from agent_driver.scheduler.schedule import Schedule, ScheduleError
from agent_driver.scheduler.store import JobStore, ScheduledJob

JobRunner = Callable[[ScheduledJob], Awaitable[object]]
Clock = Callable[[], datetime]

_STATUS_OK = "ok"
_STATUS_ERROR = "error"
_STATUS_TIMEOUT = "timeout"
_STATUS_DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class JobRunReport:
    """Outcome of one job execution within a tick."""

    job_name: str
    status: str
    detail: str | None = None


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class Scheduler:
    """Fires due jobs from a :class:`JobStore` via a host ``JobRunner``."""

    def __init__(
        self,
        store: JobStore,
        runner: JobRunner,
        *,
        clock: Clock | None = None,
        default_timeout_seconds: float = 300.0,
        max_consecutive_failures: int = 5,
    ) -> None:
        self._store = store
        self._runner = runner
        self._clock = clock or _utc_now
        self._default_timeout_seconds = default_timeout_seconds
        self._max_consecutive_failures = max_consecutive_failures

    def ensure_scheduled(self, now: datetime) -> None:
        """Seed ``next_run_at`` for enabled jobs that lack one.

        A job whose schedule cannot be parsed is disabled rather than left to
        crash every tick.
        """
        for job in self._store.list():
            if not job.enabled or job.next_run_at is not None:
                continue
            try:
                next_run = Schedule.parse(job.schedule).next_after(now)
            except ScheduleError as exc:
                self._store.update(
                    job.model_copy(
                        update={
                            "enabled": False,
                            "last_status": _STATUS_DISABLED,
                            "last_error": f"invalid schedule: {exc}",
                        }
                    )
                )
                continue
            self._store.update(job.model_copy(update={"next_run_at": next_run}))

    async def tick(self, now: datetime | None = None) -> list[JobRunReport]:
        """Run every job due at ``now`` (defaults to the clock)."""
        moment = now or self._clock()
        self.ensure_scheduled(moment)
        reports: list[JobRunReport] = []
        for job in self._store.due(moment):
            reports.append(await self._run_one(job, moment))
        return reports

    async def run_forever(
        self, *, poll_interval_seconds: float = 30.0, stop: asyncio.Event | None = None
    ) -> None:
        """Drive :meth:`tick` on an interval until ``stop`` is set."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await self.tick(self._clock())
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_one(self, job: ScheduledJob, now: datetime) -> JobRunReport:
        timeout = self._timeout_for(job)
        status = _STATUS_OK
        detail: str | None = None
        try:
            await asyncio.wait_for(self._runner(job), timeout=timeout)
        except asyncio.TimeoutError:
            status, detail = _STATUS_TIMEOUT, f"exceeded {timeout}s hard interrupt"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # A failing job must not abort the whole tick; record and move on.
            status, detail = _STATUS_ERROR, str(exc)
        self._store.update(self._advance(job, now, status, detail))
        return JobRunReport(job_name=job.job_name, status=status, detail=detail)

    def _advance(
        self, job: ScheduledJob, now: datetime, status: str, detail: str | None
    ) -> ScheduledJob:
        failed = status != _STATUS_OK
        consecutive = job.consecutive_failures + 1 if failed else 0
        update: dict[str, object] = {
            "last_run_at": now,
            "last_status": status,
            "last_error": detail if failed else None,
            "run_count": job.run_count + 1,
            "consecutive_failures": consecutive,
        }
        if failed and consecutive >= self._max_consecutive_failures:
            update["enabled"] = False
            update["next_run_at"] = None
        else:
            try:
                update["next_run_at"] = Schedule.parse(job.schedule).next_after(now)
            except ScheduleError as exc:
                update["enabled"] = False
                update["next_run_at"] = None
                update["last_status"] = _STATUS_DISABLED
                update["last_error"] = f"invalid schedule: {exc}"
        return job.model_copy(update=update)

    def _timeout_for(self, job: ScheduledJob) -> float:
        raw = job.metadata.get("timeout_seconds")
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw)
        return self._default_timeout_seconds


__all__ = ["Clock", "JobRunReport", "JobRunner", "Scheduler"]
