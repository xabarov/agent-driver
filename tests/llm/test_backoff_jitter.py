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
