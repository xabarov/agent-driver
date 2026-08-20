"""BUG-6: shared char↔token estimation + per-run calibration from real usage."""

from agent_driver.context.token_estimation import (
    DEFAULT_CHARS_PER_TOKEN,
    MAX_CHARS_PER_TOKEN,
    MIN_CHARS_PER_TOKEN,
    calibrate_chars_per_token,
    chars_for_tokens,
    estimate_tokens,
)


def test_estimate_tokens_matches_legacy_default():
    assert estimate_tokens(400) == 100  # 400 // 4.0
    assert estimate_tokens(400, chars_per_token=2.0) == 200
    assert chars_for_tokens(100) == 400
    assert chars_for_tokens(100, chars_per_token=2.0) == 200


def test_calibration_ema_moves_toward_observation():
    # observed = 200/100 = 2.0; EMA(0.7*4 + 0.3*2) = 3.4
    out = calibrate_chars_per_token(4.0, chars_sent=200, actual_input_tokens=100)
    assert abs(out - 3.4) < 1e-9


def test_calibration_ignores_degenerate_observation():
    assert calibrate_chars_per_token(4.0, chars_sent=200, actual_input_tokens=0) == 4.0
    assert (
        calibrate_chars_per_token(4.0, chars_sent=200, actual_input_tokens=None) == 4.0
    )
    assert calibrate_chars_per_token(4.0, chars_sent=0, actual_input_tokens=100) == 4.0


def test_calibration_clamps_extreme_observation():
    # observed 1000/10 = 100 -> clamped to MAX before EMA; result stays <= MAX
    out = calibrate_chars_per_token(4.0, chars_sent=10_000, actual_input_tokens=10)
    assert out <= MAX_CHARS_PER_TOKEN
    assert out > 4.0  # pulled upward
    # observed 100/100 = 1.0 -> clamped up to MIN before EMA
    low = calibrate_chars_per_token(4.0, chars_sent=100, actual_input_tokens=100)
    assert low >= MIN_CHARS_PER_TOKEN
    assert low < 4.0  # pulled downward


def test_default_ratio_is_four():
    assert DEFAULT_CHARS_PER_TOKEN == 4.0


# --------------------------------------------------------------------------- #
# BUG-6 phase-2: the optional TokenCounter seam.
# --------------------------------------------------------------------------- #


def test_calibrated_counter_matches_char_estimate():
    from agent_driver.context.token_estimation import CalibratedTokenCounter

    counter = CalibratedTokenCounter()  # default ratio 4.0
    assert counter.count_tokens("x" * 400) == 100
    # A calibrated (denser) ratio yields more tokens for the same text.
    assert CalibratedTokenCounter(chars_per_token=2.0).count_tokens("x" * 400) == 200


def test_calibrated_counter_clamps_ratio():
    from agent_driver.context.token_estimation import (
        CalibratedTokenCounter,
        MIN_CHARS_PER_TOKEN,
    )

    # A ratio below the sane floor is clamped, so it cannot explode the count.
    counter = CalibratedTokenCounter(chars_per_token=0.1)
    assert counter.count_tokens("x" * 400) == int(400 // MIN_CHARS_PER_TOKEN)


def test_count_tokens_dispatches_to_injected_counter():
    from agent_driver.context.token_estimation import TokenCounter, count_tokens

    class _Fixed:
        def count_tokens(self, text: str) -> int:
            return 7

    fixed = _Fixed()
    assert isinstance(fixed, TokenCounter)  # runtime_checkable structural match
    assert count_tokens("anything", counter=fixed) == 7
    # No counter → the char estimate (dependency-free default).
    assert count_tokens("x" * 400) == 100
    assert count_tokens("x" * 400, chars_per_token=2.0) == 200
