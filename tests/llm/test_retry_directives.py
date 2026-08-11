"""Server retry directives (F3): x-should-retry + rate-limit-reset headers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_driver.llm.retry_directives import (
    RESET_DELAY_CAP_SECONDS,
    parse_should_retry,
    rate_limit_reset_seconds,
    strongest_retry_delay,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("maybe", None),
        ("", None),
    ],
)
def test_parse_should_retry(value: str, expected: bool | None) -> None:
    assert parse_should_retry({"x-should-retry": value}) is expected


def test_parse_should_retry_absent_is_none() -> None:
    assert parse_should_retry({}) is None
    assert parse_should_retry({"other": "true"}) is None


def _now() -> float:
    return 1000.0


def test_reset_relative_seconds() -> None:
    assert rate_limit_reset_seconds({"x-ratelimit-reset-requests": "5"}, now=_now) == 5.0


def test_reset_epoch_timestamp() -> None:
    # value >= 1e6 is read as an absolute epoch → seconds until.
    assert rate_limit_reset_seconds(
        {"x-ratelimit-reset": "2000020"}, now=lambda: 2000000.0
    ) == pytest.approx(20.0)


def test_reset_iso8601_timestamp() -> None:
    base = datetime(2026, 8, 11, 0, 0, 0, tzinfo=timezone.utc)
    headers = {"anthropic-ratelimit-unified-reset": "2026-08-11T00:00:15Z"}
    assert rate_limit_reset_seconds(
        headers, now=base.timestamp
    ) == pytest.approx(15.0)


def test_reset_takes_the_latest_of_several() -> None:
    headers = {
        "x-ratelimit-reset-requests": "3",
        "x-ratelimit-reset-tokens": "9",
    }
    assert rate_limit_reset_seconds(headers, now=_now) == 9.0  # wait for the later one


def test_reset_is_capped() -> None:
    assert (
        rate_limit_reset_seconds({"x-ratelimit-reset": "9999"}, now=_now)
        == RESET_DELAY_CAP_SECONDS
    )


def test_reset_none_when_no_matching_header() -> None:
    assert rate_limit_reset_seconds({"retry-after": "5"}, now=_now) is None
    assert rate_limit_reset_seconds({"x-ratelimit-remaining": "0"}, now=_now) is None


def test_reset_ignores_unparsable_and_negative() -> None:
    assert rate_limit_reset_seconds({"x-ratelimit-reset": "soon"}, now=_now) is None
    assert rate_limit_reset_seconds({"x-ratelimit-reset": "-5"}, now=_now) is None


def test_strongest_retry_delay_takes_the_max() -> None:
    assert strongest_retry_delay(5.0, 8.0) == 8.0
    assert strongest_retry_delay(8.0, 5.0) == 8.0
    assert strongest_retry_delay(None, 8.0) == 8.0
    assert strongest_retry_delay(3.0, None) == 3.0
    assert strongest_retry_delay(None, None) is None
