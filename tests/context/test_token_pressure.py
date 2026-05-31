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
