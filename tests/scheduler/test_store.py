"""Tests for the durable scheduler job store."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_driver.scheduler import (
    InMemoryJobStore,
    JobExistsError,
    ScheduledJob,
    SqliteJobStore,
)


def _job(
    name: str, *, next_run: datetime | None = None, enabled: bool = True
) -> ScheduledJob:
    return ScheduledJob(
        job_name=name,
        schedule="@daily",
        command="sync",
        enabled=enabled,
        next_run_at=next_run,
    )


def _dt(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 6, 9, h, mi, tzinfo=timezone.utc)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryJobStore()
    else:
        s = SqliteJobStore(path=str(tmp_path / "jobs.sqlite3"))
        yield s
        s.close()


def test_add_get_delete(store) -> None:
    store.add(_job("a"))
    assert store.get("a").job_name == "a"
    assert store.delete("a") is True
    assert store.get("a") is None
    assert store.delete("a") is False


def test_add_duplicate_raises(store) -> None:
    store.add(_job("a"))
    with pytest.raises(JobExistsError):
        store.add(_job("a"))


def test_list_sorted(store) -> None:
    store.add(_job("b"))
    store.add(_job("a"))
    assert [j.job_name for j in store.list()] == ["a", "b"]


def test_due_filters_enabled_and_time(store) -> None:
    store.add(_job("past", next_run=_dt(10)))
    store.add(_job("future", next_run=_dt(14)))
    store.add(_job("disabled", next_run=_dt(10), enabled=False))
    store.add(_job("unscheduled", next_run=None))
    due = [j.job_name for j in store.due(_dt(12))]
    assert due == ["past"]


def test_update_replaces(store) -> None:
    store.add(_job("a"))
    store.update(
        store.get("a").model_copy(update={"command": "changed", "run_count": 3})
    )
    refreshed = store.get("a")
    assert refreshed.command == "changed"
    assert refreshed.run_count == 3


def test_sqlite_durable_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "jobs.sqlite3")
    store = SqliteJobStore(path=path)
    store.add(_job("nightly", next_run=_dt(1)))
    store.close()

    reopened = SqliteJobStore(path=path)
    job = reopened.get("nightly")
    assert job is not None
    assert job.schedule == "@daily"
    assert job.next_run_at == _dt(1)
    reopened.close()
