"""Decorrelated backoff jitter (F1)."""

from __future__ import annotations

import pytest

from agent_driver.llm import backoff
from agent_driver.llm.backoff import DEFAULT_JITTER_RATIO, jittered_delay


def test_jitter_is_additive_within_ratio_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backoff, "_rand", lambda: 0.0)
    assert jittered_delay(4.0) == 4.0  # rand=0 → exactly the base delay
    monkeypatch.setattr(backoff, "_rand", lambda: 1.0)
    assert jittered_delay(4.0) == pytest.approx(4.0 * (1 + DEFAULT_JITTER_RATIO))
    monkeypatch.setattr(backoff, "_rand", lambda: 0.5)
    assert jittered_delay(4.0) == pytest.approx(4.5)


def test_jitter_never_undercuts_the_base_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    # Additive-only: a server-directed Retry-After encoded in `delay` is preserved.
    for r in (0.0, 0.3, 0.9, 1.0):
        monkeypatch.setattr(backoff, "_rand", lambda r=r: r)
        assert jittered_delay(7.5) >= 7.5


def test_jitter_bounded_above_by_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backoff, "_rand", lambda: 1.0)  # worst case
    assert jittered_delay(10.0, ratio=0.25) == pytest.approx(12.5)
    assert jittered_delay(10.0, ratio=0.5) == pytest.approx(15.0)


def test_zero_and_negative_delay_return_zero() -> None:
    assert jittered_delay(0.0) == 0.0
    assert jittered_delay(-3.0) == 0.0


def test_negative_ratio_clamped_to_no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backoff, "_rand", lambda: 1.0)
    assert jittered_delay(4.0, ratio=-1.0) == 4.0


def test_default_rand_produces_value_in_window() -> None:
    # Real RNG (no patch): result must land in [delay, delay*1.25].
    for _ in range(50):
        out = jittered_delay(8.0)
        assert 8.0 <= out <= 8.0 * (1 + DEFAULT_JITTER_RATIO)


# --- abort-aware sleep (F5) ------------------------------------------------- #

import asyncio
import time

from agent_driver.llm.backoff import abort_aware_sleep


@pytest.mark.asyncio
async def test_abort_aware_sleep_returns_early_when_aborted() -> None:
    t0 = time.monotonic()
    await abort_aware_sleep(5.0, abort_check=lambda: True, poll_seconds=0.01)
    assert time.monotonic() - t0 < 0.5  # did not wait the full 5s


@pytest.mark.asyncio
async def test_abort_aware_sleep_waits_when_not_aborted() -> None:
    t0 = time.monotonic()
    await abort_aware_sleep(0.15, abort_check=lambda: False, poll_seconds=0.01)
    assert time.monotonic() - t0 >= 0.14


@pytest.mark.asyncio
async def test_abort_aware_sleep_honors_abort_mid_wait() -> None:
    ticks = {"n": 0}

    def _check() -> bool:
        ticks["n"] += 1
        return ticks["n"] >= 3  # aborts after a couple of poll slices

    t0 = time.monotonic()
    await abort_aware_sleep(5.0, abort_check=_check, poll_seconds=0.01)
    assert time.monotonic() - t0 < 0.5


@pytest.mark.asyncio
async def test_abort_aware_sleep_none_check_is_plain_sleep() -> None:
    await abort_aware_sleep(0.0, abort_check=None)  # no-op
    t0 = time.monotonic()
    await abort_aware_sleep(0.1, abort_check=None)
    assert time.monotonic() - t0 >= 0.09
