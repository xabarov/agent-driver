"""Schedule parsing and next-run computation for the scheduler.

Supports three dependency-free forms, all resolved to a :class:`Schedule`
exposing :meth:`Schedule.next_after`:

- 5-field cron ``"m h dom mon dow"`` with ``*``, lists ``1,2``, ranges
  ``1-5``, and steps ``*/5`` / ``1-10/2``;
- interval shorthand ``"every <N><unit>"`` where unit is ``s``/``m``/``h``/``d``;
- named macros ``@minutely`` / ``@hourly`` / ``@daily`` / ``@weekly`` /
  ``@monthly`` / ``@midnight``.

All computation takes the reference ``datetime`` as an argument, so it is
deterministic and unit-testable. Inputs are normalized to timezone-aware UTC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_NAMED: dict[str, str] = {
    "@minutely": "* * * * *",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}
_INTERVAL_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_INTERVAL_RE = re.compile(r"^every\s+(\d+)\s*([smhd])$", re.IGNORECASE)
# Cap the forward scan for a cron match (a valid 5-field cron always fires
# within ~a year; this bounds a pathological/impossible expression).
_CRON_SCAN_MINUTES = 367 * 24 * 60

_CRON_BOUNDS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 6),
}


class ScheduleError(ValueError):
    """Raised for an unparseable schedule expression."""


@dataclass(frozen=True, slots=True)
class Schedule:  # pylint: disable=too-many-instance-attributes
    """A parsed schedule. ``None`` cron fields mean ``*`` (any).

    A cron schedule is an inherently wide data holder (five field-sets plus the
    kind/raw/interval discriminators), so the attribute count is by design.
    """

    kind: str  # "cron" | "interval"
    raw: str
    interval_seconds: int | None = None
    minutes: frozenset[int] | None = None
    hours: frozenset[int] | None = None
    doms: frozenset[int] | None = None
    months: frozenset[int] | None = None
    dows: frozenset[int] | None = None

    @staticmethod
    def parse(text: str) -> "Schedule":
        """Parse a cron / interval / named expression into a Schedule."""
        raw = (text or "").strip()
        if not raw:
            raise ScheduleError("empty schedule")
        lowered = raw.lower()
        interval = _INTERVAL_RE.match(lowered)
        if interval is not None:
            count = int(interval.group(1))
            unit = interval.group(2).lower()
            if count <= 0:
                raise ScheduleError(f"interval must be positive: {raw!r}")
            return Schedule(
                kind="interval",
                raw=raw,
                interval_seconds=count * _INTERVAL_UNITS[unit],
            )
        expr = _NAMED.get(lowered, raw)
        return _parse_cron(expr, raw)

    def next_after(self, dt: datetime) -> datetime:
        """Return the next fire time strictly after ``dt``."""
        moment = _as_utc(dt)
        if self.kind == "interval":
            assert self.interval_seconds is not None
            return moment + timedelta(seconds=self.interval_seconds)
        # cron: scan minute-by-minute from the next whole minute.
        candidate = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(_CRON_SCAN_MINUTES):
            if self._matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ScheduleError(f"no cron match within horizon: {self.raw!r}")

    def _matches(self, moment: datetime) -> bool:
        if self.minutes is not None and moment.minute not in self.minutes:
            return False
        if self.hours is not None and moment.hour not in self.hours:
            return False
        if self.months is not None and moment.month not in self.months:
            return False
        return self._day_matches(moment)

    def _day_matches(self, moment: datetime) -> bool:
        cron_dow = moment.isoweekday() % 7  # Mon..Sun -> 1..6,0 (Sunday=0)
        dom_ok = self.doms is None or moment.day in self.doms
        dow_ok = self.dows is None or cron_dow in self.dows
        # Vixie cron: when both day-of-month and day-of-week are restricted,
        # a day matches if EITHER matches; otherwise both constraints apply.
        if self.doms is not None and self.dows is not None:
            return dom_ok or dow_ok
        return dom_ok and dow_ok


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_cron(expr: str, raw: str) -> Schedule:
    fields = expr.split()
    if len(fields) != 5:
        raise ScheduleError(
            f"cron expression must have 5 fields, got {len(fields)}: {raw!r}"
        )
    minute, hour, dom, month, dow = fields
    return Schedule(
        kind="cron",
        raw=raw,
        minutes=_parse_field(minute, *_CRON_BOUNDS["minute"], raw=raw),
        hours=_parse_field(hour, *_CRON_BOUNDS["hour"], raw=raw),
        doms=_parse_field(dom, *_CRON_BOUNDS["dom"], raw=raw),
        months=_parse_field(month, *_CRON_BOUNDS["month"], raw=raw),
        dows=_parse_field(dow, *_CRON_BOUNDS["dow"], raw=raw, dow=True),
    )


def _parse_field(
    field: str, lo: int, hi: int, *, raw: str, dow: bool = False
) -> frozenset[int] | None:
    """Parse one cron field to a set of ints, or ``None`` for ``*``."""
    if field == "*":
        return None
    values: set[int] = set()
    for part in field.split(","):
        values.update(_parse_part(part, lo, hi, raw=raw, dow=dow))
    return frozenset(values)


def _parse_part(part: str, lo: int, hi: int, *, raw: str, dow: bool) -> set[int]:
    body, _, step_text = part.partition("/")
    step = 1
    if step_text:
        step = _to_int(step_text, raw)
        if step <= 0:
            raise ScheduleError(f"step must be positive in {raw!r}")
    if body == "*":
        start, end = lo, hi
    elif "-" in body:
        start_text, _, end_text = body.partition("-")
        start, end = _to_int(start_text, raw), _to_int(end_text, raw)
    else:
        start = end = _to_int(body, raw)
    values = {_normalize(value, dow) for value in range(start, end + 1, step)}
    for value in values:
        if not lo <= value <= hi:
            raise ScheduleError(f"value {value} out of range [{lo},{hi}] in {raw!r}")
    return values


def _normalize(value: int, dow: bool) -> int:
    # cron day-of-week accepts 7 as Sunday (== 0).
    if dow and value == 7:
        return 0
    return value


def _to_int(text: str, raw: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise ScheduleError(f"invalid number {text!r} in {raw!r}") from exc


__all__ = ["Schedule", "ScheduleError"]
