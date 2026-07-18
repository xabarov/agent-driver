"""Token-pressure estimation tests."""

from __future__ import annotations

from agent_driver.context.token_pressure import (
    TokenPressureInput,
    estimate_token_pressure,
)


def test_token_pressure_reports_early_warning_state() -> None:
    """Pressure should enter early_warning above the soft threshold."""
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=({"content": "x" * 2000},),
            observations=({"text_preview": "y" * 1200},),
            retained_digest_ids=("dig_1",),
            retained_artifact_ids=("art_1",),
            context_window_estimate=3000,
            warning_threshold=700,
            compact_threshold=900,
            blocking_threshold=1100,
            output_token_reserve=400,
        )
    )
    assert pressure["state"] == "early_warning"
    assert pressure["used_tokens_estimate"] > 0
    assert pressure["context_usage_ratio"] == 0.2667
    assert pressure["retained_digest_count"] == 1


def test_token_pressure_reports_context_usage_ratio() -> None:
    """Snapshot includes usage ratio against the full context window."""
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=({"content": "x" * 1000},),
            context_window_estimate=1000,
            output_token_reserve=100,
        )
    )
    assert pressure["used_tokens_estimate"] == 250
    assert pressure["context_usage_ratio"] == 0.25


def test_token_pressure_reports_delegate_or_summarize_state() -> None:
    """Pressure should guide summarization/delegation before compaction."""
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=({"content": "x" * 5600},),
            context_window_estimate=3000,
            warning_threshold=700,
            compact_threshold=2000,
            blocking_threshold=2800,
            output_token_reserve=200,
        )
    )
    assert pressure["state"] == "delegate_or_summarize"
    assert pressure["context_usage_ratio"] == 0.4667


def test_token_pressure_reports_blocking_state() -> None:
    """Pressure should enter blocking state when estimate crosses blocking threshold."""
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=({"content": "x" * 8000},),
            observations=({"text_preview": "z" * 2000},),
            context_window_estimate=3000,
            warning_threshold=700,
            compact_threshold=900,
            blocking_threshold=1000,
            output_token_reserve=200,
        )
    )
    assert pressure["state"] == "blocking"


def test_token_pressure_reports_blocking_at_emergency_ratio() -> None:
    """The emergency blocking guard also trips at 92 percent context use."""
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=({"content": "x" * 11040},),
            context_window_estimate=3000,
            warning_threshold=700,
            compact_threshold=5000,
            blocking_threshold=5000,
            output_token_reserve=200,
        )
    )
    assert pressure["state"] == "blocking"
    assert pressure["context_usage_ratio"] == 0.92


def test_trimming_settings_for_context_window_scales_thresholds():
    """Hosts with large-context models must get proportional thresholds, not the 12k defaults
    (a retrieval-heavy prompt otherwise trips compact/blocking far below model capacity)."""
    from agent_driver.runtime.single_agent.lifecycle.config_sections import TrimmingSettings

    s = TrimmingSettings.for_context_window(100_000)
    assert s.context_window_estimate == 100_000
    assert s.token_warning_threshold == 35_000
    assert s.token_compact_threshold == 75_000
    assert s.token_blocking_threshold == 92_000
    assert s.output_token_reserve == 3125  # max(1500, window // 32)
    # overrides pass through untouched
    s2 = TrimmingSettings.for_context_window(64_000, output_token_reserve=2000, trim_max_chars=9000)
    assert s2.output_token_reserve == 2000
    assert s2.trim_max_chars == 9000
    # ratios match the class defaults' shape (12k → 4200/9000/11040)
    d = TrimmingSettings.for_context_window(12_000)
    assert (d.token_warning_threshold, d.token_compact_threshold, d.token_blocking_threshold) == (
        4200,
        9000,
        11040,
    )
