"""Durable job store for the scheduler (in-memory + SQLite backends)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ScheduledJob(ContractModel):
    """One durable scheduled job keyed by ``job_name``."""

    job_name: str
    schedule: str
    command: str
    enabled: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    run_count: int = 0
    consecutive_failures: int = 0
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_count", "consecutive_failures")
    @classmethod
    def validate_counters(cls, value: int) -> int:
        """Validate non-negative counters."""
        return int(ensure_non_negative_int(value, field_name="job counter"))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-serializable for persistence."""
        return ensure_json_serializable(value, field_name="job metadata")


class JobExistsError(ValueError):
    """Raised when adding a job whose name is already registered."""


class JobStore(Protocol):
    """Durable backend for scheduled jobs (persistence only)."""

    def add(self, job: ScheduledJob) -> ScheduledJob:
        """Insert a new job; raise :class:`JobExistsError` if it exists."""
        raise NotImplementedError

    def update(self, job: ScheduledJob) -> ScheduledJob:
        """Insert-or-replace a job by name."""
        raise NotImplementedError

    def get(self, job_name: str) -> ScheduledJob | None:
        """Return a job by name, or ``None``."""
        raise NotImplementedError

    def delete(self, job_name: str) -> bool:
        """Delete a job; return whether a row was removed."""
        raise NotImplementedError

    def list(self) -> list[ScheduledJob]:
        """Return all jobs ordered by name."""
        raise NotImplementedError

    def due(self, now: datetime) -> list[ScheduledJob]:
        """Return enabled jobs whose ``next_run_at`` is due at ``now``."""
        raise NotImplementedError


def _is_due(job: ScheduledJob, now: datetime) -> bool:
    return job.enabled and job.next_run_at is not None and job.next_run_at <= now


class InMemoryJobStore:
    """Process-local job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._lock = RLock()

    def add(self, job: ScheduledJob) -> ScheduledJob:
        """Insert a new job; raise if the name already exists."""
        with self._lock:
            if job.job_name in self._jobs:
                raise JobExistsError(f"cron job already exists: {job.job_name}")
            self._jobs[job.job_name] = job
            return job

    def update(self, job: ScheduledJob) -> ScheduledJob:
        """Insert or replace a job by name."""
        with self._lock:
            self._jobs[job.job_name] = job
            return job

    def get(self, job_name: str) -> ScheduledJob | None:
        """Return a job by name, or ``None``."""
        with self._lock:
            return self._jobs.get(job_name)

    def delete(self, job_name: str) -> bool:
        """Delete a job; return whether one was removed."""
        with self._lock:
            return self._jobs.pop(job_name, None) is not None

    def list(self) -> list[ScheduledJob]:
        """Return all jobs ordered by name."""
        with self._lock:
            return [self._jobs[name] for name in sorted(self._jobs)]

    def due(self, now: datetime) -> list[ScheduledJob]:
        """Return enabled jobs due at ``now``."""
        return [job for job in self.list() if _is_due(job, now)]


class SqliteJobStore:
    """Durable SQLite-backed job store keyed by ``job_name``."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = RLock()
        if str(self._path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """)
            self._conn.commit()

    def add(self, job: ScheduledJob) -> ScheduledJob:
        """Insert a new job; raise if the name already exists."""
        with self._lock:
            if self._get_locked(job.job_name) is not None:
                raise JobExistsError(f"cron job already exists: {job.job_name}")
            self._write_locked(job)
            return job

    def update(self, job: ScheduledJob) -> ScheduledJob:
        """Insert or replace a job by name."""
        with self._lock:
            self._write_locked(job)
            return job

    def get(self, job_name: str) -> ScheduledJob | None:
        """Return a job by name, or ``None``."""
        with self._lock:
            return self._get_locked(job_name)

    def delete(self, job_name: str) -> bool:
        """Delete a job; return whether a row was removed."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM jobs WHERE job_name = ?", (job_name,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def list(self) -> list[ScheduledJob]:
        """Return all jobs ordered by name."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM jobs ORDER BY job_name"
            ).fetchall()
        return [ScheduledJob.model_validate(json.loads(row[0])) for row in rows]

    def due(self, now: datetime) -> list[ScheduledJob]:
        """Return enabled jobs due at ``now``."""
        return [job for job in self.list() if _is_due(job, now)]

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()

    def _get_locked(self, job_name: str) -> ScheduledJob | None:
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE job_name = ?", (job_name,)
        ).fetchone()
        if row is None:
            return None
        return ScheduledJob.model_validate(json.loads(row[0]))

    def _write_locked(self, job: ScheduledJob) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO jobs (job_name, payload) VALUES (?, ?)",
            (job.job_name, json.dumps(job.model_dump(mode="json"))),
        )
        self._conn.commit()


__all__ = [
    "InMemoryJobStore",
    "JobExistsError",
    "JobStore",
    "ScheduledJob",
    "SqliteJobStore",
]
