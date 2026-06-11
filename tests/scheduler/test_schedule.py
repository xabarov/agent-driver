"""Tests for cron/interval schedule parsing and next_after computation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_driver.scheduler.schedule import Schedule, ScheduleError


def _dt(y, mo, d, h, mi, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def test_interval_next_after() -> None:
    sch = Schedule.parse("every 5m")
    assert sch.kind == "interval"
    assert sch.interval_seconds == 300
    assert sch.next_after(_dt(2026, 6, 9, 12, 0, 30)) == _dt(2026, 6, 9, 12, 5, 30)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("every 30s", 30), ("every 2h", 7200), ("every 1d", 86400)],
)
def test_interval_units(text: str, seconds: int) -> None:
    assert Schedule.parse(text).interval_seconds == seconds


def test_daily_macro() -> None:
    sch = Schedule.parse("@daily")
    nxt = sch.next_after(_dt(2026, 6, 9, 12, 30))
    assert nxt == _dt(2026, 6, 10, 0, 0)


def test_hourly_macro_next_minute_boundary() -> None:
    sch = Schedule.parse("@hourly")
    assert sch.next_after(_dt(2026, 6, 9, 12, 30)) == _dt(2026, 6, 9, 13, 0)


def test_cron_every_minute_strictly_after() -> None:
    sch = Schedule.parse("* * * * *")
    # Strictly after: 12:00:30 -> 12:01:00.
    assert sch.next_after(_dt(2026, 6, 9, 12, 0, 30)) == _dt(2026, 6, 9, 12, 1)
    # On the minute boundary it still advances to the next minute.
    assert sch.next_after(_dt(2026, 6, 9, 12, 1, 0)) == _dt(2026, 6, 9, 12, 2)


def test_cron_specific_time() -> None:
    sch = Schedule.parse("30 9 * * *")
    assert sch.next_after(_dt(2026, 6, 9, 9, 0)) == _dt(2026, 6, 9, 9, 30)
    assert sch.next_after(_dt(2026, 6, 9, 9, 30)) == _dt(2026, 6, 10, 9, 30)


def test_cron_step_and_list() -> None:
    sch = Schedule.parse("*/15 * * * *")
    assert sch.minutes == frozenset({0, 15, 30, 45})
    assert sch.next_after(_dt(2026, 6, 9, 12, 5)) == _dt(2026, 6, 9, 12, 15)
    listed = Schedule.parse("0,30 * * * *")
    assert listed.minutes == frozenset({0, 30})


def test_cron_range() -> None:
    sch = Schedule.parse("0 9-17 * * *")
    assert sch.hours == frozenset(range(9, 18))


def test_cron_dow_sunday_seven_equals_zero() -> None:
    assert Schedule.parse("0 0 * * 7").dows == frozenset({0})
    sch = Schedule.parse("0 0 * * 0")  # Sunday
    # 2026-06-14 is a Sunday.
    assert sch.next_after(_dt(2026, 6, 9, 0, 0)) == _dt(2026, 6, 14, 0, 0)


def test_cron_dom_dow_or_semantics() -> None:
    # Both restricted -> fire on the 1st OR on Monday (Vixie cron OR rule).
    sch = Schedule.parse("0 0 1 * 1")
    # 2026-06-09 is a Tuesday; next is Mon 2026-06-15... but the 1st? June 1
    # already passed; next Monday is 2026-06-15. 0:00 on that Monday.
    nxt = sch.next_after(_dt(2026, 6, 9, 12, 0))
    assert nxt == _dt(2026, 6, 15, 0, 0)
    # And the 1st of next month fires even though it is not a Monday.
    nxt2 = sch.next_after(_dt(2026, 6, 30, 12, 0))
    assert nxt2 == _dt(2026, 7, 1, 0, 0)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "1 2 3",
        "every 0m",
        "60 * * * *",
        "* 25 * * *",
        "a b c d e",
        "*/0 * * * *",
    ],
)
def test_invalid_schedules_raise(bad: str) -> None:
    with pytest.raises(ScheduleError):
        Schedule.parse(bad)
